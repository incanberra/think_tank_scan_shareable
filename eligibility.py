"""Deterministic eligibility rules; model decisions cannot override these."""
from urllib.parse import urlparse

import content_extractor
import source_discovery


def exclusion_reason(item, require_publication=False):
    if item.get("last_reported_run_date") or item.get("last_emailed_run_date"):
        return "already_reported"
    url = item.get("canonical_url") or item.get("url") or ""
    if item.get("is_listing_page") or source_discovery.native_rejection_reason(url, item.get("title", "")):
        return "not_individual_content"
    parsed = urlparse(url)
    host, path = parsed.hostname or "", parsed.path.lower()
    # These are programme/project landing pages, not publication detail pages.
    if ((host.endswith("sipri.org") or host.endswith("cnas.org")) and path.startswith("/research/")) or (
        host.endswith("rand.org") and "/research/projects/" in path
    ):
        return "programme_or_project_page"
    if item.get("item_type") == "event" or item.get("content_type_guess") == "event":
        start = content_extractor.parse_date(item.get("event_start_at"))
        run_date = item.get("scan_run_date") or item.get("last_seen_run_date")
        if not start or not run_date:
            return "event_date_unverified"
        _, cutoff = content_extractor.coverage_window(run_date)
        if item.get("event_start_precision") == "date":
            past = start.date() < cutoff.date()
        else:
            past = start < cutoff
        return "past_event" if past else ""
    if item.get("date_status") == "verified_out_of_window":
        return "publication_out_of_window"
    if item.get("date_crosscheck_status") == "publication_date_changed" and item.get("seen_status") == "seen_before":
        return "publication_date_conflict"
    if require_publication and (
        item.get("date_status") != "verified_in_window"
        or item.get("date_source") not in {"page_publication", "rss_published", "index_publication"}
    ):
        return "publication_date_unverified"
    return ""


def filter_for_publication(analyzed_data, output_dir, run_date):
    """Recheck persisted history immediately before rendering, including aliases."""
    import seen_ledger

    ledger = seen_ledger.load_ledger(output_dir)
    emitted = set()
    for category in ("reports", "podcasts", "events"):
        kept = []
        for item in analyzed_data.get(category, []):
            candidate = dict(item, scan_run_date=run_date)
            if category == "events":
                candidate["item_type"] = "event"
            key = seen_ledger.lookup_item_key(ledger["items"], candidate)
            entry = ledger["items"].get(key, {})
            # Multiple model outputs in ONE scan may contain the same new item.
            same_run = bool(candidate.get("scan_run_id")) and entry.get("last_reported_run_id") == candidate["scan_run_id"]
            if not same_run:
                candidate["last_reported_run_date"] = entry.get("last_reported_run_date") or candidate.get("last_reported_run_date")
                candidate["last_emailed_run_date"] = entry.get("last_emailed_run_date") or candidate.get("last_emailed_run_date")
            reason = exclusion_reason(candidate, require_publication=True)
            if key in emitted:
                reason = "duplicate_in_report"
            if reason:
                analyzed_data.setdefault("excluded", []).append(dict(item, exclusion_reason=reason))
            else:
                emitted.add(key)
                kept.append(item)
        analyzed_data[category] = kept
    return analyzed_data
