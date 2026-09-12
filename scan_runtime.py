"""Run identities, durable pending work, discovery checkpoints and decision cache.

State is stored beside the existing ledger and protected by its process lock.
No production state is written merely by importing this module.
"""
import copy
import hashlib
import json
import os
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from state_storage import atomic_json_write

_active = ContextVar("scanner_run", default=None)
PROMPT_VERSION = "economic-security-v4-evidence-sections"
RETRY_REASONS = {"publication_date_unverified", "date_unknown_seen_before_skipped",
                 "event_date_unverified", "insufficient_evidence", "source_review_cap",
                 "model_review_failed", "model_response_invalid", "publication_date_conflict"}


def current():
    return _active.get()


def source_state(status):
    text = str(status).lower()
    failed = any(word in text for word in ("fail", "unavailable", "blocked", "403", "429", "not checked"))
    if failed:
        return "degraded" if " found " in text or "/found " in text else "unavailable"
    return "empty" if any(word in text for word in ("no qualifying", "no raw", "no content", "no recent")) else "available"


def item_key(item):
    import seen_ledger
    return seen_ledger.identity_key(*seen_ledger.item_identity(item))


def serializable(value):
    return json.loads(json.dumps(value, default=lambda obj: obj.isoformat() if isinstance(obj, datetime) else str(obj)))


class ScanRun:
    def __init__(self, root, run_date, cutoff=None, reprocess=False, retry_pending=False, historical=False):
        self.root = Path(root).resolve()
        self.started = datetime.now(ZoneInfo(config.TIMEZONE_CANBERRA))
        self.run_date = run_date
        self.run_id = self.started.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
        self.output_dir = self.root / "runs" / self.run_id
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.cutoff = cutoff or datetime.fromisoformat(run_date).replace(hour=3, tzinfo=self.started.tzinfo)
        self.reprocess = reprocess
        self.historical = historical
        self.state_path = Path(config.SEEN_LEDGER_PATH).resolve().with_name("workflow_state.json")
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if self.state.get("version") != 1 or any(not isinstance(self.state.get(k), dict) for k in ("pending", "backlog", "decisions", "checkpoints")):
                    raise ValueError("Unexpected workflow state format")
            except Exception as exc:
                raise RuntimeError(f"Cannot read workflow state; restore a validated backup: {self.state_path}") from exc
        else:
            if Path(str(self.state_path) + ".bak").exists():
                raise RuntimeError("Workflow state is missing but its backup exists; restore it first")
            self.state = {"version": 1, "pending": {}, "backlog": {}, "decisions": {}, "checkpoints": {}}
        if retry_pending:
            for row in self.state["pending"].values():
                row.update(status="pending", attempts=0, next_attempt_at=self.started.isoformat())
        import seen_ledger
        self.ledger = seen_ledger.load_ledger(str(self.root))
        self.prior_delivered = sum(any(r.get("run_date") == run_date and r.get("status") == "emailed" for r in e.get("delivery_history", [])) for e in self.ledger["items"].values())
        self.windows = {}
        self.metrics = Counter()
        self.timings = {}
        self.network = {}
        self.outcomes = []
        self.status_notes = {}
        self.pending_attempted = set()
        self.native_queue_keys = set()
        self.models = []
        self.save_manifest("running")

    def window(self, source=None):
        start = self.cutoff - timedelta(hours=config.COVERAGE_WINDOW_HOURS)
        if source and not self.historical:
            checkpoint = self.state["checkpoints"].get(source)
            if checkpoint:
                start = min(start, datetime.fromisoformat(checkpoint) - timedelta(hours=config.CHECKPOINT_OVERLAP_HOURS))
        self.windows[source or "default"] = {"start": start.isoformat(), "end": self.cutoff.isoformat()}
        return start, self.cutoff

    def already_reported(self, item):
        import seen_ledger
        key = seen_ledger.lookup_item_key(self.ledger["items"], item)
        entry = self.ledger["items"].get(key, {})
        return bool(entry.get("last_reported_run_date") or entry.get("last_emailed_run_date"))

    def persist(self):
        if self.state_path.exists():
            old = json.loads(self.state_path.read_text(encoding="utf-8"))
            atomic_json_write(str(self.state_path) + ".bak", old)
        atomic_json_write(str(self.state_path), self.state)

    def due(self, entry):
        return entry.get("status") == "pending" and datetime.fromisoformat(entry["next_attempt_at"]) <= self.started

    def pending_items(self):
        rows = sorted(self.state["pending"].items(), key=lambda kv: kv[1].get("next_attempt_at", ""))
        selected = []
        source_counts = Counter()
        for key, entry in rows:
            if not self.due(entry):
                continue
            item = entry["item"]
            source = item.get("institution", "Unknown")
            if self.already_reported(item):
                entry["status"] = "resolved_reported"
                continue
            if source_counts[source] >= config.PENDING_RETRIES_PER_SOURCE:
                continue
            source_counts[source] += 1
            selected.append(dict(item, pending_retry=True, force_refresh=True))
            self.pending_attempted.add(key)
        self.metrics["pending_retries"] = len(selected)
        return selected

    def prepare_candidates(self, candidates):
        merged = {}
        for item in candidates + self.pending_items():
            key = item_key(item)
            row = dict(merged.get(key, {}))
            row.update(item)
            pending = self.state["pending"].get(key)
            if pending and pending.get("status") in {"pending", "paused"}:
                row["pending_existing"] = True
                if (not self.due(pending) or key not in self.pending_attempted) and not self.reprocess:
                    row["pending_waiting"] = True
                elif pending.get("status") == "pending":
                    row.update(pending_retry=True, force_refresh=True)
            merged[key] = row
        return list(merged.values())

    def cap_native(self, items):
        from source_discovery import candidate_priority
        by_key = {item_key(item): item for item in items}
        source = items[0].get("institution") if items else None
        if source:
            for key, entry in self.state["backlog"].items():
                if entry["item"].get("institution") == source:
                    by_key.setdefault(key, entry["item"])
        available = []
        for key, item in by_key.items():
            import eligibility
            # URL-only programme/listing exclusions are safe before fetching.
            # Event and date eligibility require enrichment, so keep those here.
            reason = eligibility.exclusion_reason(item)
            if reason in {"programme_or_project_page", "not_individual_content"}:
                self.metrics["non_content_removed_before_cap"] += 1
                self.state["backlog"].pop(key, None)
                continue
            if self.already_reported(item):
                self.metrics["reported_removed_before_cap"] += 1
                self.state["backlog"].pop(key, None)
                continue
            pending = self.state["pending"].get(key)
            if pending and (not self.due(pending) or pending.get("status") == "paused") and not self.reprocess:
                self.metrics["pending_waiting_before_cap"] += 1
                continue
            available.append(item)
        # Oldest deferred candidates get a turn before fresh arrivals.
        available.sort(key=lambda i: (0, self.state["backlog"][item_key(i)]["deferred_at"]) if item_key(i) in self.state["backlog"] else (1, ""))
        backlog_items = [i for i in available if item_key(i) in self.state["backlog"]]
        fresh = sorted([i for i in available if item_key(i) not in self.state["backlog"]], key=candidate_priority, reverse=True)
        ordered = backlog_items + fresh
        if len(ordered) > config.MAX_BACKLOG_PER_SOURCE:
            raise RuntimeError(f"Discovery backlog for {source} exceeds {config.MAX_BACKLOG_PER_SOURCE}; drain or review retained work before expanding discovery")
        chosen = ordered[:config.MAX_NATIVE_CANDIDATES_PER_SOURCE]
        for item in ordered:
            key = item_key(item)
            self.state["backlog"].setdefault(key, {"item": serializable(item), "deferred_at": self.started.isoformat()})
        for item in chosen:
            item["native_queue_key"] = item_key(item)
        self.native_queue_keys.update(item_key(i) for i in chosen)
        self.metrics["native_deferred"] += max(0, len(ordered) - len(chosen))
        self.persist()  # Keep deferred and selected work even if enrichment crashes.
        return chosen, max(0, len(ordered) - len(chosen))

    def remember_pending(self, item, reason):
        key = item_key(item)
        entry = self.state["pending"].get(key, {})
        # Avoid counting one candidate repeatedly for comparison models or audits.
        attempts = int(entry.get("attempts", 0)) + (entry.get("last_attempt_run") != self.run_id)
        delay = min(2 ** max(0, attempts - 1), 7)
        payload = dict(entry.get("item", {}))
        payload.update({k: item.get(k) for k in ("url", "canonical_url", "institution", "source_domain", "title", "summary", "published_at", "date_source", "item_type", "discovery_methods") if item.get(k) is not None})
        self.state["pending"][key] = {"item": serializable(payload), "reason": reason, "attempts": attempts,
            "status": "paused" if attempts >= config.PENDING_MAX_ATTEMPTS else "pending",
            "first_queued_at": entry.get("first_queued_at", self.started.isoformat()),
            "last_attempt_run": self.run_id, "next_attempt_at": (self.started + timedelta(days=delay)).isoformat()}

    def record_selection(self, items):
        for item in items:
            key = item_key(item)
            queue_key = item.get("native_queue_key") or key
            if queue_key in self.native_queue_keys:
                self.state["backlog"].pop(queue_key, None)
            reason = item.get("review_selection_reason", "")
            if reason in RETRY_REASONS or (item.get("review_selected") and item.get("date_status") == "date_unknown"):
                self.remember_pending(item, reason or "publication_date_unverified")
            elif not item.get("review_selected") and reason != "pending_retry_not_due":
                self.state["pending"].pop(key, None)
        self.persist()

    def record_analysis(self, data):
        self.outcomes.append(data)
        # Aggregate across models: any unresolved outcome keeps work pending.
        pending_keys = set()
        resolved_keys = set()
        for result in self.outcomes:
            for item in result.get("needs_review", []):
                self.remember_pending(item, item.get("exclusion_reason") or "model_review_failed")
                pending_keys.add(item_key(item))
            for category in ("reports", "events", "podcasts", "excluded"):
                resolved_keys.update(item_key(i) for i in result.get(category, []))
        for key in resolved_keys - pending_keys:
            self.state["pending"].pop(key, None)
        self.persist()

    def decision_key(self, item, model):
        packet = {k: item.get(k) for k in ("url", "canonical_url", "title", "summary", "extracted_text", "published_at_verified", "date_source", "event_start_at", "evidence_quality")}
        import topic_utils
        packet.update(model=model, prompt_version=PROMPT_VERSION, ontology=topic_utils.build_topic_prompt_block(), text_budget=config.LLM_ITEM_TEXT_CHAR_LIMIT)
        if item.get("rescue_status") == "recovered":
            packet.update(rescue_version=1, rescue_text_budget=config.RESCUE_TEXT_CHAR_LIMIT,
                          rescue_evidence_url=item.get("rescue_evidence_url"))
        return hashlib.sha256(json.dumps(packet, sort_keys=True, default=str).encode()).hexdigest()

    def get_decision(self, item, model):
        if self.reprocess:
            return None
        entry = self.state["decisions"].get(self.decision_key(item, model))
        if entry:
            self.metrics["decision_cache_hits"] += 1
            return copy.deepcopy(entry["analysis"])
        return None

    def cache_decision(self, item, model, analysis):
        self.state["decisions"][self.decision_key(item, model)] = {"analysis": analysis, "saved_at": self.started.isoformat(), "model": model, "prompt_version": PROMPT_VERSION}
        if len(self.state["decisions"]) > config.MAX_CACHED_DECISIONS:
            oldest = min(self.state["decisions"], key=lambda k: self.state["decisions"][k]["saved_at"])
            del self.state["decisions"][oldest]
        self.persist()

    @contextmanager
    def stage(self, name):
        start = time.monotonic()
        try:
            yield
        finally:
            self.timings[name] = round(time.monotonic() - start, 3)

    def metadata(self):
        return {"run_id": self.run_id, "run_date": self.run_date, "started_at": self.started.isoformat(),
            "coverage_end": self.cutoff.isoformat(), "source_windows": self.windows, "models": self.models,
            "prompt_version": PROMPT_VERSION, "prior_delivered_today": self.prior_delivered,
            "edition": "supplement" if self.prior_delivered else "daily", "stage_seconds": self.timings,
            "metrics": dict(self.metrics), "pending_status_counts": dict(Counter(i["status"] for i in self.state["pending"].values())),
            "discovery_backlog": len(self.state["backlog"]), "source_status": self.status_notes, "network": self.network, "model_requests": getattr(self, "model_requests", [])}

    def save_manifest(self, status, **extra):
        atomic_json_write(str(self.output_dir / "pending_review.json"), self.state["pending"])
        atomic_json_write(str(self.output_dir / "run.json"), {**self.metadata(), "status": status, **extra})

    def finish(self, email_sent):
        if not self.historical:
            for source, status in self.status_notes.items():
                if source_state(status) in {"available", "empty"}:
                    old = self.state["checkpoints"].get(source)
                    if not old or datetime.fromisoformat(old) < self.cutoff:
                        self.state["checkpoints"][source] = self.cutoff.isoformat()
        self.persist()
        self.save_manifest("complete", completed_at=datetime.now(self.started.tzinfo).isoformat(), email_sent=email_sent)
        atomic_json_write(str(self.root / "latest.json"), {"run_id": self.run_id, "directory": str(self.output_dir), "manifest": str(self.output_dir / "run.json")})


@contextmanager
def activate(run):
    token = _active.set(run)
    try:
        yield run
    except BaseException as exc:
        run.persist()
        run.save_manifest("failed", error=str(exc)[:300])
        raise
    finally:
        _active.reset(token)
