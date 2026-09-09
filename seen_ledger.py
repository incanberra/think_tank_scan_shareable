import hashlib
import json
import os
import re
import uuid
import scan_runtime

from state_storage import atomic_json_write, locked
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import config


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
LEDGER_VERSION = 3


def ledger_path(output_dir):
    return os.path.abspath(config.SEEN_LEDGER_PATH)


def ledger_audit_copy_path(output_dir):
    return os.path.join(output_dir, "audit", "seen_items.json")


def normalize_url(url):
    if not url:
        return ""
    parsed = urlparse(str(url).strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    remainder = f"{parsed.netloc}{parsed.path}".lower()
    if "http://" in remainder or "https://" in remainder or parsed.netloc.lower().endswith(("http:", "https:")):
        return ""
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_KEYS or key_lower.startswith(TRACKING_QUERY_PREFIXES):
            continue
        query_pairs.append((key, value))
    # Sort unique query keys only: repeated-key ordering can be meaningful.
    if len({key for key, _ in query_pairs}) == len(query_pairs):
        query_pairs.sort()
    clean_query = urlencode(query_pairs)
    path = parsed.path.rstrip("/") or "/"
    # Decode unreserved characters only; encoded slashes retain their meaning.
    def normalize_escape(match):
        char = chr(int(match.group(0)[1:], 16))
        return char if char.isascii() and (char.isalnum() or char in "-._~") else match.group(0).upper()
    path = re.sub(r"%[0-9a-fA-F]{2}", normalize_escape, path)
    host = parsed.netloc.lower()
    try:
        port = parsed.port
    except ValueError:
        return ""
    if (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}:
        host = host.rsplit(":", 1)[0]
    return urlunparse(
        (
            parsed.scheme.lower(),
            host,
            path,
            parsed.params,
            clean_query,
            "",
        )
    )


def normalize_title(title):
    return re.sub(r"\s+", " ", str(title or "").strip().lower())


def item_identity(item):
    url = normalize_url(item.get("canonical_url")) or normalize_url(item.get("url"))
    if url:
        return "url", url

    institution = normalize_title(item.get("institution"))
    title = normalize_title(item.get("title") or item.get("extracted_title"))
    return "title", f"{institution}|{title}"


def identity_key(identity_kind, identity_value):
    digest = hashlib.sha256(f"{identity_kind}:{identity_value}".encode("utf-8")).hexdigest()
    return f"{identity_kind}:{digest}"


def empty_ledger():
    return {
        "version": LEDGER_VERSION,
        "items": {},
        "backfill": {},
    }


def candidate_urls_from_entry(entry):
    for value in [entry.get("identity_value")]:
        if value:
            yield value
    for value in entry.get("urls") or []:
        if value:
            yield value
    for history_key in ["report_history", "delivery_history"]:
        for record in entry.get(history_key) or []:
            if isinstance(record, dict) and record.get("url"):
                yield record["url"]


def merge_ledger_entries(existing, incoming):
    for field in ["institutions", "titles", "urls"]:
        for value in incoming.get(field) or []:
            existing[field] = sorted(set(existing.get(field, [])) | {value}) if field == "urls" else bounded_append(existing.get(field), value)
    for field in ["date_status_history", "report_history", "delivery_history"]:
        for value in incoming.get(field) or []:
            existing[field] = bounded_append(existing.get(field), value, limit=20)
    for field in [
        "first_seen_run_date",
        "first_seen_at",
        "first_verified_publication_date",
        "content_hash",
    ]:
        if incoming.get(field) and (not existing.get(field) or str(incoming[field]) < str(existing[field])):
            existing[field] = incoming[field]
    for field in [
        "last_seen_run_date",
        "last_seen_at",
        "last_verified_publication_date",
        "last_content_hash",
        "last_reported_run_date",
        "last_emailed_run_date",
    ]:
        if incoming.get(field) and str(incoming[field]) > str(existing.get(field, "")):
            existing[field] = incoming[field]
    existing["seen_run_count"] = max(int(existing.get("seen_run_count") or 0), int(incoming.get("seen_run_count") or 0))
    return existing


def repair_malformed_url_identities(data):
    items = data.setdefault("items", {})
    repaired = {}
    changed = False

    for key, entry in items.items():
        if not isinstance(entry, dict):
            changed = True
            continue
        identity_kind = entry.get("identity_kind", "url")
        identity_value = entry.get("identity_value", "")
        new_key = key

        if identity_kind == "url":
            normalized_value = ""
            for candidate_url in candidate_urls_from_entry(entry):
                normalized_value = normalize_url(candidate_url)
                if normalized_value:
                    break
            if not normalized_value:
                raise ValueError("Ledger contains an unrepairable URL identity; refusing to discard history")
            entry["identity_value"] = normalized_value
            new_key = identity_key("url", normalized_value)
            changed = changed or new_key != key or normalized_value != identity_value

        if new_key in repaired:
            merge_ledger_entries(repaired[new_key], entry)
            changed = True
        else:
            repaired[new_key] = entry

    if changed:
        data["items"] = repaired
        data.setdefault("maintenance", {})
        data["maintenance"]["malformed_url_identity_repair"] = {
            "ran_at": datetime.utcnow().isoformat() + "Z",
            "items_after": len(repaired),
        }
    return data


def validate_ledger(data):
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise ValueError("Ledger must contain an items object")
    if int(data.get("version") or 1) > LEDGER_VERSION:
        raise ValueError("Ledger was written by a newer scanner")
    for key, entry in data["items"].items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise ValueError("Invalid ledger entry")
        if entry.get("identity_kind") not in {"url", "title"} or not isinstance(entry.get("identity_value"), str):
            raise ValueError("Invalid ledger identity")
        for field in ("urls", "institutions", "titles", "report_history", "delivery_history", "date_status_history"):
            if field in entry and not isinstance(entry[field], list):
                raise ValueError("Invalid ledger history: " + field)
    return data


@locked
def load_ledger(output_dir):
    path = ledger_path(output_dir)
    if not os.path.exists(path):
        if os.path.exists(path + ".bak") or os.path.exists(ledger_audit_copy_path(output_dir)):
            raise RuntimeError("Seen ledger is missing but historical state exists. Restore it before scanning: " + path)
        return empty_ledger()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = validate_ledger(json.load(handle))
    except Exception as exc:
        raise RuntimeError("Cannot read seen ledger; refusing to forget report history. Restore a validated backup: " + path) from exc
    data["version"] = LEDGER_VERSION
    data.setdefault("backfill", {})
    data = repair_malformed_url_identities(data)
    return coalesce_alias_entries(data)


@locked
def save_ledger(output_dir, ledger):
    validate_ledger(ledger)
    path = ledger_path(output_dir)
    if os.path.exists(path):
        # Never overwrite corrupt state or its last good backup.
        with open(path, "r", encoding="utf-8") as handle:
            previous = validate_ledger(json.load(handle))
        atomic_json_write(path + ".bak", previous)
    atomic_json_write(path, ledger)
    if not os.path.exists(path + ".bak"):
        atomic_json_write(path + ".bak", ledger)
    audit_copy = ledger_audit_copy_path(output_dir)
    if os.path.abspath(audit_copy) != path:
        atomic_json_write(audit_copy, ledger)
    return path


def item_urls(item):
    # Aliases are observed request, redirect and publisher canonical URLs.
    return {url for value in [item.get("url"), item.get("canonical_url"), item.get("resolved_url")]
            if (url := normalize_url(value))}


def lookup_item_key(entries, item):
    key = item.get("seen_item_key")
    if key in entries:
        return key
    urls = item_urls(item)
    key = identity_key(*item_identity(item))
    if key in entries:
        return key
    for existing_key, entry in entries.items():
        aliases = {normalize_url(value) for value in candidate_urls_from_entry(entry)}
        if urls & aliases:
            return existing_key
    return key


def coalesce_alias_entries(ledger):
    entries = ledger["items"]
    aliases = {}
    for key in list(entries):
        if key not in entries:
            continue
        entry = entries[key]
        urls = {normalize_url(value) for value in candidate_urls_from_entry(entry)} - {""}
        matches = {aliases[url] for url in urls if url in aliases and aliases[url] in entries}
        for other in matches - {key}:
            merge_ledger_entries(entry, entries.pop(other))
            for url, owner in list(aliases.items()):
                if owner == other:
                    aliases[url] = key
        for url in urls:
            aliases[url] = key
    return ledger


def bounded_append(values, value, limit=12):
    if not value:
        return values or []
    updated = list(values or [])
    if value not in updated:
        updated.append(value)
    return updated[-limit:]


def current_verified_publication_date(item):
    if item.get("published_at_verified"):
        return item["published_at_verified"]
    if item.get("date_status") in ("verified_in_window", "verified_out_of_window"):
        return item.get("extracted_date") or item.get("date") or ""
    return ""


def content_hash_for_item(item):
    if item.get("content_hash"):
        return item["content_hash"]
    text = re.sub(r"\s+", " ", str(item.get("extracted_text", "")).strip().lower())
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def date_key(value):
    if not value:
        return ""
    text = str(value)
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    return text.strip().lower()


def classify_date_crosscheck(entry, item, run_date_str, current_verified_date, content_changed=False):
    date_status = item.get("date_status", "not_checked")
    first_seen = entry.get("first_seen_run_date") or run_date_str
    first_verified = entry.get("first_verified_publication_date", "")

    if current_verified_date and first_verified and date_key(current_verified_date) != date_key(first_verified):
        return (
            "publication_date_changed",
            f"Current verified publication date differs from first verified date ({first_verified}).",
        )
    if date_status == "verified_in_window":
        return "verified_publication_date_in_window", "Publication date was verified against page/feed metadata."
    if date_status == "verified_out_of_window":
        return "verified_publication_date_out_of_window", "Publication date was verified and falls outside this run window."
    if date_status == "date_unknown" and first_seen == run_date_str:
        return "date_unknown_first_seen_this_run", "Publication date is unknown, but the item was first discovered in this run."
    if date_status == "date_unknown" and content_changed:
        return (
            "date_unknown_seen_before_content_changed",
            f"Publication date is unknown and the item was first seen on {first_seen}, but extracted content changed.",
        )
    if date_status == "date_unknown":
        return (
            "date_unknown_seen_before",
            f"Publication date is unknown and the item was first seen on {first_seen}.",
        )
    return "date_not_checked", "Publication date was not checked."


def new_entry(item, identity_kind, identity_value, run_date_str):
    now = datetime.utcnow().isoformat() + "Z"
    return {
        "identity_kind": identity_kind,
        "identity_value": identity_value,
        "first_seen_run_date": run_date_str,
        "last_seen_run_date": run_date_str,
        "first_seen_at": now,
        "last_seen_at": now,
        "seen_run_count": 0,
        "institutions": [],
        "titles": [],
        "urls": [],
        "first_verified_publication_date": "",
        "last_verified_publication_date": "",
        "content_hash": "",
        "last_content_hash": "",
        "date_status_history": [],
        "report_history": [],
        "delivery_history": [],
        "last_reported_run_date": "",
        "last_emailed_run_date": "",
    }


@locked
def annotate_items_with_seen_metadata(items, output_dir, run_date_str):
    """
    Adds persistent first-seen/date cross-check metadata to candidates and
    updates the output directory's seen-items ledger.
    """
    ledger = load_ledger(output_dir)
    items_by_key = ledger.setdefault("items", {})
    preexisting_keys = set(items_by_key.keys())
    scan_run_id = scan_runtime.current().run_id if scan_runtime.current() else uuid.uuid4().hex
    updated_this_run = set()
    new_count = 0
    seen_before_count = 0
    date_crosscheck_counts = {}

    for item in items:
        identity_kind, identity_value = item_identity(item)
        key = lookup_item_key(items_by_key, item)
        entry = items_by_key.get(key)
        was_seen_before = key in preexisting_keys
        if entry is None:
            entry = new_entry(item, identity_kind, identity_value, run_date_str)
            items_by_key[key] = entry

        current_verified = current_verified_publication_date(item)
        current_hash = content_hash_for_item(item)
        previous_hash = entry.get("last_content_hash") or entry.get("content_hash") or ""
        content_changed = bool(was_seen_before and current_hash and previous_hash and current_hash != previous_hash)
        if current_verified and not entry.get("first_verified_publication_date"):
            entry["first_verified_publication_date"] = current_verified
        if current_verified:
            entry["last_verified_publication_date"] = current_verified
        if current_hash and not entry.get("content_hash"):
            entry["content_hash"] = current_hash
        if current_hash:
            entry["last_content_hash"] = current_hash

        status, note = classify_date_crosscheck(entry, item, run_date_str, current_verified, content_changed)
        date_crosscheck_counts[status] = date_crosscheck_counts.get(status, 0) + 1
        if content_changed:
            date_crosscheck_counts["content_hash_changed"] = date_crosscheck_counts.get("content_hash_changed", 0) + 1

        item["scan_run_id"] = scan_run_id
        item["seen_item_key"] = key
        item["seen_identity_kind"] = identity_kind
        item["seen_identity_value"] = identity_value
        item["seen_status"] = "seen_before" if was_seen_before else "new_this_run"
        item["first_seen_run_date"] = entry["first_seen_run_date"]
        item["last_seen_run_date"] = run_date_str
        item["seen_run_count"] = entry.get("seen_run_count", 0) + (0 if key in updated_this_run else 1)
        item["first_verified_publication_date"] = entry.get("first_verified_publication_date", "")
        item["last_verified_publication_date"] = entry.get("last_verified_publication_date", "")
        item["content_hash"] = current_hash
        item["previous_content_hash"] = previous_hash
        item["content_changed_since_last_seen"] = content_changed
        item["date_crosscheck_status"] = status
        item["date_crosscheck_note"] = note
        item["last_reported_run_date"] = entry.get("last_reported_run_date", "")
        item["last_emailed_run_date"] = entry.get("last_emailed_run_date", "")

        if was_seen_before:
            seen_before_count += 1
        else:
            new_count += 1

        entry["institutions"] = bounded_append(entry.get("institutions"), item.get("institution"))
        entry["titles"] = bounded_append(entry.get("titles"), item.get("title") or item.get("extracted_title"))
        entry["urls"] = sorted(set(entry.get("urls", [])) | item_urls(item))
        entry["last_seen_run_date"] = run_date_str
        entry["last_seen_at"] = datetime.utcnow().isoformat() + "Z"

        if key not in updated_this_run:
            entry["seen_run_count"] = entry.get("seen_run_count", 0) + 1
            entry["date_status_history"] = bounded_append(
                entry.get("date_status_history"),
                {
                    "run_date": run_date_str,
                    "date_status": item.get("date_status", "not_checked"),
                    "date_crosscheck_status": status,
                    "verified_publication_date": current_verified,
                    "content_hash": current_hash,
                    "content_changed": content_changed,
                },
                limit=20,
            )
            updated_this_run.add(key)

    path = save_ledger(output_dir, ledger)
    return {
        "seen_ledger_path": path,
        "seen_total_items": len(items_by_key),
        "new_this_run": new_count,
        "seen_before": seen_before_count,
        "date_crosscheck_counts": dict(sorted(date_crosscheck_counts.items())),
        "ledger_version": LEDGER_VERSION,
        "audit_copy_path": ledger_audit_copy_path(output_dir),
        "backfill": ledger.get("backfill", {}),
    }


def iter_included_items(analyzed_data):
    for category in ["reports", "podcasts", "events"]:
        for item in analyzed_data.get(category, []):
            yield category, item


@locked
def mark_reported_items(output_dir, run_date_str, model_slug, analyzed_data):
    ledger = load_ledger(output_dir)
    items_by_key = ledger.setdefault("items", {})
    reported_count = 0

    for category, item in iter_included_items(analyzed_data):
        key = lookup_item_key(items_by_key, item)
        entry = items_by_key.get(key)
        if entry is None:
            identity_kind, identity_value = item_identity(item)
            entry = new_entry(item, identity_kind, identity_value, run_date_str)
            items_by_key[key] = entry

        report_record = {
            "run_date": run_date_str,
            "model": model_slug,
            "category": category,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "importance_score": item.get("importance_score"),
        }
        entry["report_history"] = bounded_append(entry.get("report_history"), report_record, limit=20)
        entry["last_reported_run_date"] = run_date_str
        entry["last_reported_run_id"] = item.get("scan_run_id", "")
        reported_count += 1

    path = save_ledger(output_dir, ledger)
    return {
        "seen_ledger_path": path,
        "reported_count": reported_count,
        "seen_total_items": len(items_by_key),
    }


@locked
def mark_delivery_items(output_dir, run_date_str, model_slug, analyzed_data, delivery_status="emailed"):
    ledger = load_ledger(output_dir)
    items_by_key = ledger.setdefault("items", {})
    delivered_count = 0

    for category, item in iter_included_items(analyzed_data):
        key = lookup_item_key(items_by_key, item)
        entry = items_by_key.get(key)
        if entry is None:
            identity_kind, identity_value = item_identity(item)
            entry = new_entry(item, identity_kind, identity_value, run_date_str)
            items_by_key[key] = entry

        delivery_record = {
            "run_date": run_date_str,
            "model": model_slug,
            "category": category,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "status": delivery_status,
        }
        entry["delivery_history"] = bounded_append(entry.get("delivery_history"), delivery_record, limit=20)
        if delivery_status == "emailed":
            entry["last_emailed_run_date"] = run_date_str
        delivered_count += 1

    path = save_ledger(output_dir, ledger)
    return {
        "seen_ledger_path": path,
        "delivered_count": delivered_count,
        "seen_total_items": len(items_by_key),
    }


def run_date_from_enriched_filename(path):
    match = re.search(r"enriched_candidates_(\d{4}-\d{2}-\d{2})\.jsonl$", os.path.basename(path))
    return match.group(1) if match else ""


@locked
def backfill_seen_ledger_from_audits(output_dir, audit_dir=None):
    explicit_audit_dir = audit_dir
    audit_dir = audit_dir or os.path.join(output_dir, "audit")
    ledger = load_ledger(output_dir)
    items_by_key = ledger.setdefault("items", {})
    files = []
    if os.path.isdir(audit_dir):
        files = sorted(
            os.path.join(audit_dir, name)
            for name in os.listdir(audit_dir)
            if re.match(r"enriched_candidates_\d{4}-\d{2}-\d{2}\.jsonl$", name)
        )

    if not explicit_audit_dir:
        from pathlib import Path
        files.extend(str(p) for p in Path(output_dir, "runs").glob("*/audit/enriched_candidates_*.jsonl"))
        files = sorted(set(files), key=lambda p: (run_date_from_enriched_filename(p), p))

    created = 0
    updated = 0
    rows_seen = 0
    earliest_run = ""
    latest_run = ""

    for path in files:
        run_date_str = run_date_from_enriched_filename(path)
        if not run_date_str:
            continue
        earliest_run = min(earliest_run, run_date_str) if earliest_run else run_date_str
        latest_run = max(latest_run, run_date_str) if latest_run else run_date_str
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows_seen += 1
                identity_kind, identity_value = item_identity(item)
                key = identity_key(identity_kind, identity_value)
                entry = items_by_key.get(key)
                if entry is None:
                    entry = new_entry(item, identity_kind, identity_value, run_date_str)
                    items_by_key[key] = entry
                    created += 1
                elif run_date_str < entry.get("first_seen_run_date", run_date_str):
                    entry["first_seen_run_date"] = run_date_str
                    updated += 1

                current_verified = current_verified_publication_date(item)
                current_hash = content_hash_for_item(item)
                if current_verified and not entry.get("first_verified_publication_date"):
                    entry["first_verified_publication_date"] = current_verified
                if current_verified:
                    entry["last_verified_publication_date"] = current_verified
                if current_hash and not entry.get("content_hash"):
                    entry["content_hash"] = current_hash
                if current_hash:
                    entry["last_content_hash"] = current_hash
                entry["last_seen_run_date"] = max(entry.get("last_seen_run_date", run_date_str), run_date_str)
                entry["institutions"] = bounded_append(entry.get("institutions"), item.get("institution"))
                entry["titles"] = bounded_append(entry.get("titles"), item.get("title") or item.get("extracted_title"))
                entry["urls"] = sorted(set(entry.get("urls", [])) | item_urls(item))

    ledger["backfill"] = {
        "completed_at": datetime.utcnow().isoformat() + "Z",
        "source_audit_dir": audit_dir,
        "files_processed": len(files),
        "rows_seen": rows_seen,
        "items_created": created,
        "items_updated_to_earlier_first_seen": updated,
        "earliest_run_date": earliest_run,
        "latest_run_date": latest_run,
    }
    path = save_ledger(output_dir, ledger)
    return {
        "seen_ledger_path": path,
        "audit_copy_path": ledger_audit_copy_path(output_dir),
        "seen_total_items": len(items_by_key),
        **ledger["backfill"],
    }
