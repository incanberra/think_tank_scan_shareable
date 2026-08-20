import json
import os
from collections import defaultdict
from datetime import datetime

import config
import topic_utils


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, default=json_default)


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=json_default))
            handle.write("\n")


def summarize_candidates(items):
    by_source = defaultdict(int)
    by_method = defaultdict(int)
    extraction_status = defaultdict(int)
    date_status = defaultdict(int)
    date_crosscheck_status = defaultdict(int)
    content_changed = 0
    no_text = 0
    no_topic_hint = 0
    seen_before_unknown_date = 0

    for item in items:
        by_source[item.get("institution", "Unknown")] += 1
        for method in item.get("discovery_methods") or [item.get("discovery_method", "unknown")]:
            by_method[method] += 1
        extraction_status[item.get("extraction_status", "not_enriched")] += 1
        date_status[item.get("date_status", "not_checked")] += 1
        date_crosscheck_status[item.get("date_crosscheck_status", "not_checked")] += 1
        if not item.get("extracted_text"):
            no_text += 1
        if not item.get("topic_hints"):
            no_topic_hint += 1
        if item.get("date_crosscheck_status") == "date_unknown_seen_before":
            seen_before_unknown_date += 1
        if item.get("content_changed_since_last_seen"):
            content_changed += 1

    missing_sources = [
        source
        for source in config.THINK_TANKS
        if by_source.get(source, 0) == 0
    ]

    return {
        "total_candidates": len(items),
        "by_source": dict(sorted(by_source.items())),
        "by_discovery_method": dict(sorted(by_method.items())),
        "extraction_status": dict(sorted(extraction_status.items())),
        "date_status": dict(sorted(date_status.items())),
        "date_crosscheck_status": dict(sorted(date_crosscheck_status.items())),
        "items_without_extracted_text": no_text,
        "items_without_topic_hints": no_topic_hint,
        "seen_before_with_unknown_date": seen_before_unknown_date,
        "content_hash_changed": content_changed,
        "sources_with_no_candidates": missing_sources,
        "topic_hints": topic_utils.summarize_topic_hints(items),
    }


def count_by_source(items):
    counts = defaultdict(int)
    for item in items or []:
        counts[item.get("institution", "Unknown")] += 1
    return counts


def build_source_health(raw_items, enriched_items, status_notes, enrichment_audit, analyzed_data=None):
    """
    Builds per-source metrics that can be compared across daily runs.
    """
    raw_by_source = count_by_source(raw_items)
    enriched_by_source = count_by_source(enriched_items)
    selected_by_source = defaultdict(int)
    skipped_by_source = defaultdict(int)
    no_text_by_source = defaultdict(int)
    date_unknown_by_source = defaultdict(int)
    extraction_fail_by_source = defaultdict(int)
    extraction_ok_by_source = defaultdict(int)

    failing_extraction_statuses = {
        "fetch_failed",
        "http_failed",
        "skipped_non_html",
        "skipped_pdf_missing_dependency",
        "pdf_extract_failed",
        "empty_text",
    }

    for item in enriched_items or []:
        source = item.get("institution", "Unknown")
        reason = item.get("review_selection_reason", "")
        if reason.startswith("date_verified_out_of_window") or reason.startswith("low_signal") or reason in {
            "source_review_cap",
            "date_unknown_seen_before_skipped",
        }:
            skipped_by_source[source] += 1
        elif reason:
            selected_by_source[source] += 1
        if not item.get("extracted_text"):
            no_text_by_source[source] += 1
        if item.get("date_status") == "date_unknown":
            date_unknown_by_source[source] += 1
        extraction_status = item.get("extraction_status")
        if extraction_status == "ok":
            extraction_ok_by_source[source] += 1
        elif extraction_status in failing_extraction_statuses:
            extraction_fail_by_source[source] += 1

    included_by_source = defaultdict(int)
    excluded_by_source = defaultdict(int)
    review_by_source = defaultdict(int)
    if analyzed_data:
        for category in ["reports", "podcasts", "events"]:
            for item in analyzed_data.get(category, []):
                included_by_source[item.get("institution", "Unknown")] += 1
        for item in analyzed_data.get("excluded", []):
            excluded_by_source[item.get("institution", "Unknown")] += 1
        for item in analyzed_data.get("needs_review", []):
            review_by_source[item.get("institution", "Unknown")] += 1

    observed_sources = (
        set(config.THINK_TANKS)
        | set(raw_by_source.keys())
        | set(enriched_by_source.keys())
        | set(included_by_source.keys())
        | set(excluded_by_source.keys())
        | set(review_by_source.keys())
    )
    rows = []
    for source in sorted(observed_sources):
        raw_count = raw_by_source.get(source, 0)
        enriched_count = enriched_by_source.get(source, 0)
        no_text = no_text_by_source.get(source, 0)
        date_unknown = date_unknown_by_source.get(source, 0)
        extraction_failures = extraction_fail_by_source.get(source, 0)
        rows.append(
            {
                "source": source,
                "status": status_notes.get(source, "not checked"),
                "raw_candidates": raw_count,
                "enriched_candidates": enriched_count,
                "selected_for_review": selected_by_source.get(source, 0),
                "skipped_before_review": skipped_by_source.get(source, 0),
                "included": included_by_source.get(source, 0),
                "excluded": excluded_by_source.get(source, 0),
                "needs_review": review_by_source.get(source, 0),
                "extraction_ok": extraction_ok_by_source.get(source, 0),
                "extraction_failures": extraction_failures,
                "items_without_text": no_text,
                "date_unknown": date_unknown,
                "text_gap_rate": round(no_text / enriched_count, 3) if enriched_count else None,
                "date_unknown_rate": round(date_unknown / enriched_count, 3) if enriched_count else None,
            }
        )

    return {
        "generated_at": datetime.now().isoformat(),
        "enrichment_cache": enrichment_audit.get("cache_status_counts", {}),
        "sources": rows,
    }


def build_recall_audit(raw_items, enriched_items, status_notes, enrichment_audit):
    """
    Creates a run-level audit object focused on recall risks.
    """
    raw_summary = summarize_candidates(raw_items)
    enriched_summary = summarize_candidates(enriched_items)
    source_status = {
        source: status_notes.get(source, "not checked")
        for source in config.THINK_TANKS
    }
    risk_flags = []
    risk_details = []

    if enriched_summary["items_without_extracted_text"]:
        risk_flags.append(
            f"{enriched_summary['items_without_extracted_text']} candidates had no extracted page text"
        )
        risk_details.append(
            {
                "severity": "medium",
                "area": "Page extraction",
                "issue": f"{enriched_summary['items_without_extracted_text']} candidates had no extracted page text",
                "implication": "The LLM had to rely on feed/index metadata or snippets for those candidates.",
            }
        )
    if enriched_summary["date_status"].get("date_unknown"):
        risk_flags.append(
            f"{enriched_summary['date_status']['date_unknown']} candidates had uncertain publication dates"
        )
        risk_details.append(
            {
                "severity": "medium",
                "area": "Date verification",
                "issue": f"{enriched_summary['date_status']['date_unknown']} candidates had uncertain publication dates",
                "implication": "Some relevant items may need manual date confirmation against source pages.",
            }
        )
    if enriched_summary.get("seen_before_with_unknown_date"):
        risk_flags.append(
            f"{enriched_summary['seen_before_with_unknown_date']} candidates had unknown dates and were seen in earlier runs"
        )
        risk_details.append(
            {
                "severity": "medium",
                "area": "First-seen date cross-check",
                "issue": (
                    f"{enriched_summary['seen_before_with_unknown_date']} candidates had unknown publication dates "
                    "and were not first seen in this run"
                ),
                "implication": "These candidates are less likely to be newly published and are filtered before review unless another rule keeps them in scope.",
            }
        )
    native_discovery = enrichment_audit.get("discovery", {}).get("native_discovery", {})
    native_rejection_counts = native_discovery.get("native_rejection_counts", {})
    native_rejected_total = native_discovery.get("native_rejected_total", 0)
    if native_rejected_total:
        risk_flags.append(f"{native_rejected_total} native-discovery links were rejected before enrichment")
        risk_details.append(
            {
                "severity": "low",
                "area": "Native discovery hygiene",
                "issue": f"{native_rejected_total} source-native links were rejected as non-content",
                "status": ", ".join(f"{key}: {value}" for key, value in sorted(native_rejection_counts.items())),
                "implication": "The scanner filtered obvious sitemap, navigation, pagination, and listing links before enrichment to protect model budget.",
            }
        )

    review_selection = enrichment_audit.get("review_selection", {})
    reason_counts = review_selection.get("reason_counts") or review_selection.get("review_selection_reason_counts", {})
    skipped_seen_before = reason_counts.get("skipped:date_unknown_seen_before_skipped", 0)
    changed_seen_before = reason_counts.get("selected:date_unknown_seen_before_content_changed", 0)
    if skipped_seen_before:
        risk_flags.append(f"{skipped_seen_before} undated seen-before candidates were skipped as likely stale")
    if changed_seen_before:
        risk_flags.append(f"{changed_seen_before} undated seen-before candidates changed and were reviewed")

    for row in build_source_health(raw_items, enriched_items, status_notes, enrichment_audit).get("sources", []):
        if row.get("raw_candidates", 0) > 0 and row.get("selected_for_review", 0) == 0:
            risk_details.append(
                {
                    "severity": "low",
                    "area": row["source"],
                    "issue": "Candidates found but none selected for model review",
                    "status": row.get("status", ""),
                    "implication": "Candidates were filtered before LLM review because they were stale, low-signal, capped, or outside the verified date window.",
                }
            )
    if enriched_summary["sources_with_no_candidates"]:
        risk_flags.append(
            f"{len(enriched_summary['sources_with_no_candidates'])} sources returned no candidates"
        )
        for source in enriched_summary["sources_with_no_candidates"]:
            status = source_status.get(source, "not checked")
            severity = "low"
            implication = "No relevant or recent candidates were discovered by configured methods."
            status_lower = status.lower()
            if "not checked" in status_lower:
                severity = "high"
                implication = "The source was not checked by any configured discovery method."
            elif any(token in status_lower for token in ["blocked", "403", "failed", "unavailable"]):
                severity = "high"
                implication = "Discovery may be incomplete because the source or fallback path was blocked or unavailable."
            elif "no raw candidates" in status_lower:
                severity = "medium"
                implication = "Native discovery found no raw candidates; this can be normal, but it is a recall watch item."
            risk_details.append(
                {
                    "severity": severity,
                    "area": source,
                    "issue": "No candidates discovered",
                    "status": status,
                    "implication": implication,
                }
            )

    return {
        "raw_candidate_summary": raw_summary,
        "enriched_candidate_summary": enriched_summary,
        "source_status": source_status,
        "enrichment_audit": enrichment_audit,
        "seen_ledger": enrichment_audit.get("seen_ledger", {}),
        "native_discovery": native_discovery,
        "review_selection": review_selection,
        "source_health": build_source_health(raw_items, enriched_items, status_notes, enrichment_audit),
        "recall_risk_flags": risk_flags,
        "recall_risk_details": risk_details,
    }


def append_source_health_history(output_dir, run_date_str, model_slug, source_health):
    history_path = os.path.join(output_dir, "audit", "source_health_history.jsonl")
    row = {
        "run_date": run_date_str,
        "model": model_slug or "pre_analysis",
        "generated_at": source_health.get("generated_at"),
        "sources": source_health.get("sources", []),
    }
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=json_default))
        handle.write("\n")
    return history_path


def save_source_health_audit(
    output_dir,
    run_date_str,
    model_slug,
    raw_items,
    enriched_items,
    status_notes,
    enrichment_audit,
    analyzed_data=None,
):
    source_health = build_source_health(raw_items, enriched_items, status_notes, enrichment_audit, analyzed_data)
    suffix = f"_{model_slug}" if model_slug else ""
    path = os.path.join(output_dir, "audit", f"source_health_{run_date_str}{suffix}.json")
    write_json(path, source_health)
    history_path = append_source_health_history(output_dir, run_date_str, model_slug, source_health)
    return {"source_health_path": path, "source_health_history_path": history_path, "source_health": source_health}


def save_run_audit(output_dir, run_date_str, raw_items, enriched_items, status_notes, enrichment_audit):
    audit_dir = os.path.join(output_dir, "audit")
    raw_path = os.path.join(audit_dir, f"raw_candidates_{run_date_str}.jsonl")
    enriched_path = os.path.join(audit_dir, f"enriched_candidates_{run_date_str}.jsonl")
    audit_path = os.path.join(audit_dir, f"recall_audit_{run_date_str}.json")

    write_jsonl(raw_path, raw_items)
    write_jsonl(enriched_path, enriched_items)
    recall_audit = build_recall_audit(raw_items, enriched_items, status_notes, enrichment_audit)
    write_json(audit_path, recall_audit)
    pre_analysis_source_health = save_source_health_audit(
        output_dir,
        run_date_str,
        None,
        raw_items,
        enriched_items,
        status_notes,
        enrichment_audit,
    )

    return {
        "raw_candidates_path": raw_path,
        "enriched_candidates_path": enriched_path,
        "recall_audit_path": audit_path,
        "recall_audit": recall_audit,
        "source_health_path": pre_analysis_source_health["source_health_path"],
        "source_health_history_path": pre_analysis_source_health["source_health_history_path"],
        "seen_ledger_path": enrichment_audit.get("seen_ledger", {}).get("seen_ledger_path"),
    }


def save_analysis_audit(output_dir, run_date_str, model_slug, analyzed_data):
    audit_dir = os.path.join(output_dir, "audit")
    decisions = {
        "model": model_slug,
        "included_counts": {
            "reports": len(analyzed_data.get("reports", [])),
            "podcasts": len(analyzed_data.get("podcasts", [])),
            "events": len(analyzed_data.get("events", [])),
        },
        "excluded": analyzed_data.get("excluded", []),
        "needs_review": analyzed_data.get("needs_review", []),
    }
    path = os.path.join(audit_dir, f"analysis_decisions_{run_date_str}_{model_slug}.json")
    write_json(path, decisions)
    return path
