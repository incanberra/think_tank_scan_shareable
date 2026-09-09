from collections import defaultdict
from urllib.parse import urlparse

import config
import eligibility


def should_review_candidate(item):
    """
    Chooses which enriched candidates should go to the LLM.

    Raw and enriched candidates remain in audit logs. This selector only prevents
    clearly stale or low-signal native-discovery links from consuming model time.
    """
    if item.get("pending_waiting"):
        return False, "pending_retry_not_due"
    blocked = eligibility.exclusion_reason(item)
    if blocked:
        return False, blocked
    methods = set(item.get("discovery_methods") or [item.get("discovery_method", "")])
    date_status = item.get("date_status", "not_checked")
    item_type = item.get("item_type") or item.get("content_type_guess") or "report"
    seen_status = item.get("seen_status", "")
    date_crosscheck_status = item.get("date_crosscheck_status", "")
    content_changed = bool(item.get("content_changed_since_last_seen"))
    topic_hints = item.get("topic_hints") or []
    extracted_chars = int(item.get("extracted_text_chars") or 0)
    title = item.get("title") or ""

    if item.get("evidence_quality") == "insufficient":
        return False, "insufficient_evidence"
    if item.get("pending_retry") and date_status == "date_unknown":
        return False, "publication_date_unverified"
    if "rss" in methods:
        return True, "rss_in_window"
    if date_status == "verified_in_window":
        return True, "date_verified_in_window"
    if date_status == "verified_out_of_window" and item_type != "event":
        return False, "date_verified_out_of_window"
    if date_crosscheck_status == "date_unknown_seen_before_content_changed" and item_type != "event":
        return True, "date_unknown_seen_before_content_changed"
    if date_crosscheck_status == "date_unknown_seen_before" and item_type != "event":
        return False, "date_unknown_seen_before_skipped"
    if date_status == "date_unknown" and seen_status == "seen_before" and not content_changed and item_type != "event":
        return False, "date_unknown_seen_before_skipped"
    if topic_hints and extracted_chars >= 300:
        return True, "date_uncertain_with_topic_hints_and_text"
    if topic_hints and len(title) >= 20:
        return True, "date_uncertain_with_topic_hints"
    return False, "low_signal_or_date_uncertain"


def is_official_report_like_url(item):
    url = item.get("canonical_url") or item.get("url") or ""
    parsed = urlparse(url)
    path = parsed.path.lower()
    source_domain = str(item.get("source_domain") or "").lower()
    host = parsed.netloc.lower()
    if source_domain and not (host == source_domain or host.endswith("." + source_domain)):
        return False
    item_type = item.get("item_type") or item.get("content_type_guess") or "report"
    if item_type == "event":
        return False
    return any(
        token in path
        for token in [
            "/analysis",
            "/article",
            "/commentary",
            "/paper",
            "/papers",
            "/publication",
            "/publications",
            "/report",
            "/reports",
            "/research",
            "/digest",
        ]
    )


def date_poor_sources(items):
    by_source = defaultdict(lambda: {"total": 0, "date_unknown": 0})
    for item in items:
        source = item.get("institution", "Unknown")
        by_source[source]["total"] += 1
        if item.get("date_status") == "date_unknown":
            by_source[source]["date_unknown"] += 1
    poor = set()
    for source, counts in by_source.items():
        total = counts["total"]
        if not total:
            continue
        if counts["date_unknown"] / total >= config.DATE_POOR_SOURCE_THRESHOLD:
            poor.add(source)
    return poor


def should_sample_date_poor_candidate(item, date_poor_source_names):
    if item.get("pending_waiting") or item.get("evidence_quality") == "insufficient" or item.get("pending_retry") or eligibility.exclusion_reason(item):
        return False
    source = item.get("institution", "Unknown")
    if source not in date_poor_source_names:
        return False
    if item.get("date_status") != "date_unknown":
        return False
    if int(item.get("extracted_text_chars") or 0) < config.DATE_POOR_SAMPLE_MIN_TEXT_CHARS:
        return False
    if not is_official_report_like_url(item):
        return False
    return item.get("seen_status") == "new_this_run" or bool(item.get("content_changed_since_last_seen"))


def select_candidates_for_review(items, max_per_source=None):
    """
    Returns selected candidates plus an audit summary. Caps only non-RSS sources.
    """
    max_per_source = max_per_source or config.MAX_REVIEW_CANDIDATES_PER_SOURCE
    selected = []
    skipped = []
    source_counts = defaultdict(int)
    date_poor_names = date_poor_sources(items)
    date_poor_sample_counts = defaultdict(int)

    for item in items:
        should_review, reason = should_review_candidate(item)
        item["review_selection_reason"] = reason
        source = item.get("institution", "Unknown")
        methods = set(item.get("discovery_methods") or [item.get("discovery_method", "")])

        if (
            not should_review
            and date_poor_sample_counts[source] < config.DATE_POOR_SOURCE_SAMPLE_PER_RUN
            and should_sample_date_poor_candidate(item, date_poor_names)
        ):
            should_review = True
            reason = "date_poor_source_new_or_changed_sample"
            item["review_selection_reason"] = reason
            date_poor_sample_counts[source] += 1

        if should_review and "rss" not in methods:
            if source_counts[source] >= max_per_source:
                should_review = False
                reason = "source_review_cap"
                item["review_selection_reason"] = reason
            else:
                source_counts[source] += 1

        item["review_selected"] = bool(should_review)
        if should_review:
            selected.append(item)
        else:
            skipped.append(item)

    reason_counts = defaultdict(int)
    for item in selected:
        reason_counts[f"selected:{item.get('review_selection_reason')}"] += 1
    for item in skipped:
        reason_counts[f"skipped:{item.get('review_selection_reason')}"] += 1

    return selected, {
        "total_enriched_candidates": len(items),
        "selected_for_review": len(selected),
        "skipped_before_review": len(skipped),
        "reason_counts": dict(sorted(reason_counts.items())),
        "review_selection_reason_counts": dict(sorted(reason_counts.items())),
        "non_rss_source_review_counts": dict(sorted(source_counts.items())),
        "date_poor_sources": sorted(date_poor_names),
        "date_poor_sample_counts": dict(sorted(date_poor_sample_counts.items())),
    }
