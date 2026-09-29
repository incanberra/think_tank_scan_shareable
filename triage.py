"""Conservative Jev routing, independent of dates and delivery history."""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import config
import editorial_policy as policy
import jev_client


def evidence_packet(item):
    return policy.packet(item, budget=config.TRIAGE_TEXT_CHAR_LIMIT)


def cache_key(item):
    value = [config.TRIAGE_MODEL, policy.RUBRIC_VERSION, policy.PACKET_VERSION, policy.POLICY,
             jev_client.QUESTIONS, evidence_packet(item),
             hashlib.sha256(str(item.get("extracted_text", "")).encode()).hexdigest(),
             item.get("canonical_url") or item.get("url")]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def route(item, result):
    answers = jev_client.validate(result)["answers"]
    adequacy, centrality = answers["adequacy"], answers["centrality"]
    if adequacy["choice"] in ("teaser", "missing") and adequacy["probabilities"][adequacy["choice"]] >= 0.9:
        return "hold", "insufficient_evidence: Jev identified a teaser or missing body"
    adequate = adequacy["choice"] == "body" or (adequacy["choice"] == "description" and
        (item.get("item_type") or item.get("content_type_guess")) in ("event", "podcast"))
    # Never automatically reject a partial long-document excerpt. Omitted passages
    # may contain the economic mechanism. Confidence is not calibrated accuracy.
    if (adequate and adequacy["probabilities"][adequacy["choice"]] >= 0.95 and
            not evidence_packet(item)["excerpt_is_partial"] and
            centrality["choice"] in ("absent", "incidental") and
            centrality["probabilities"][centrality["choice"]] >= 0.98 and
            centrality["confidence"] >= 0.95 and answers["mechanism"]["noul"] <= 0.02):
        return "exclude", "No substantive economic-security mechanism (conservative Jev consensus)"
    return "glm", "Summary or full relevance review required"


def evaluate(items, run=None):
    mode = config.TRIAGE_MODE
    if mode not in ("off", "shadow", "active"):
        raise ValueError("TRIAGE_MODE must be off, shadow or active")
    if mode == "off":
        return []
    cache = run.state.setdefault("triage_decisions", {}) if run else {}
    if run and not hasattr(run, "model_requests"):
        run.model_requests = []

    def one(item):
        record = {"url": item.get("canonical_url") or item.get("url"), "title": item.get("title"),
                  "mode": mode, "rubric_version": policy.RUBRIC_VERSION, "packet_version": policy.PACKET_VERSION,
                  "cache_key": cache_key(item), "cache_status": "miss"}
        entry = cache.get(record["cache_key"])
        try:
            if entry and not getattr(run, "reprocess", False) and time.time() - entry.get("saved_at", 0) < 7 * 86400:
                result = jev_client.validate(entry["result"])
                record["cache_status"] = "hit"
            else:
                result = jev_client.decide(evidence_packet(item), run)
            action, reason = route(item, result)
            record.update(proposed_route=action, reason=reason, result=result,
                          effective_route=action if mode == "active" else "glm")
        except Exception as exc:
            record.update(proposed_route="fallback", effective_route="glm",
                          reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        return record

    with ThreadPoolExecutor(max_workers=max(1, min(4, config.TRIAGE_CONCURRENCY))) as pool:
        records = list(pool.map(one, items))
    # Persist only on the caller thread; never race atomic state writes.
    for record in records:
        if "result" in record:
            cache[record["cache_key"]] = {"result": record["result"], "saved_at": time.time()}
    while len(cache) > config.MAX_CACHED_DECISIONS:
        del cache[min(cache, key=lambda k: cache[k]["saved_at"])]
    if run:
        run.persist()
        run.save_manifest("running")
    return records
