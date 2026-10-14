import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import ai_client
import config
import topic_utils

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


from seen_ledger import normalize_url
import eligibility
import scan_runtime
import evidence_selection
import editorial_policy


def normalize_title(title):
    return re.sub(r"\s+", " ", str(title or "").strip().lower())


GENERIC_TITLE_KEYS = {
    "analysis",
    "articles",
    "commentary",
    "current page",
    "current page 1",
    "events",
    "first page",
    "last page",
    "map room",
    "next page",
    "podcasts",
    "previous page",
    "publications",
    "reports",
    "research",
    "skip to content",
    "videos",
}


def title_is_high_signal(title_key):
    if not title_key or title_key in GENERIC_TITLE_KEYS:
        return False
    if any(title_key.startswith(prefix) for prefix in ("current page ", "go to page ", "page ")):
        return False
    words = re.findall(r"[a-z0-9]+", title_key)
    return len(title_key) >= 18 and len(words) >= 3


def title_scope(item):
    source = item.get("source_domain") or item.get("institution") or ""
    return normalize_title(source)


def merge_duplicate_item(existing, incoming):
    """
    Preserves discovery provenance when duplicate candidates are found.
    """
    for field in ["summary", "raw_summary", "extracted_text"]:
        if len(str(incoming.get(field, ""))) > len(str(existing.get(field, ""))):
            existing[field] = incoming[field]

    for field in [
        "title",
        "author",
        "date",
        "canonical_url",
        "extracted_title",
        "extracted_author",
        "extracted_date",
        "scan_run_id",
        "scan_run_date",
        "date_source",
        "published_at_verified",
        "event_start_at",
        "event_start_precision",
        "seen_item_key",
        "seen_status",
        "first_seen_run_date",
        "last_seen_run_date",
        "first_verified_publication_date",
        "last_verified_publication_date",
        "date_crosscheck_status",
        "date_crosscheck_note",
        "content_hash",
        "previous_content_hash",
        "last_reported_run_date",
        "last_emailed_run_date",
    ]:
        if incoming.get(field) and not existing.get(field):
            existing[field] = incoming[field]

    methods = set(existing.get("discovery_methods") or [])
    methods.update(incoming.get("discovery_methods") or [])
    if incoming.get("discovery_method"):
        methods.add(incoming["discovery_method"])
    existing["discovery_methods"] = sorted(methods)

    queries = set(existing.get("discovery_queries") or [])
    queries.update(incoming.get("discovery_queries") or [])
    if incoming.get("discovery_query"):
        queries.add(incoming["discovery_query"])
    existing["discovery_queries"] = sorted(queries)

    duplicate_urls = set(existing.get("duplicate_urls") or [])
    for candidate_url in [existing.get("url"), incoming.get("url"), incoming.get("canonical_url")]:
        if candidate_url:
            duplicate_urls.add(candidate_url)
    existing["duplicate_urls"] = sorted(duplicate_urls)
    return existing


def deduplicate_items(items):
    """
    Deduplicates by canonical URL/URL first, then by high-signal title within a source.
    """
    by_key = {}
    title_to_key = {}
    ordered_keys = []

    for item in items:
        canonical = normalize_url(item.get("canonical_url")) or normalize_url(item.get("url"))
        title_key = normalize_title(item.get("title") or item.get("extracted_title"))
        scoped_title_key = f"{title_scope(item)}|{title_key}" if title_is_high_signal(title_key) else ""
        key = canonical or (f"title:{scoped_title_key}" if scoped_title_key else f"item:{len(ordered_keys)}")

        if canonical and canonical in by_key:
            merge_duplicate_item(by_key[canonical], item)
            continue
        if scoped_title_key and scoped_title_key in title_to_key:
            merge_duplicate_item(by_key[title_to_key[scoped_title_key]], item)
            continue

        by_key[key] = dict(item)
        ordered_keys.append(key)
        if scoped_title_key:
            title_to_key[scoped_title_key] = key

    return [by_key[key] for key in ordered_keys]


def valid_topics(topics):
    allowed = set(config.ECONOMIC_SECURITY_TOPICS)
    cleaned = []
    for topic in topics or []:
        if topic in allowed and topic not in cleaned:
            cleaned.append(topic)
    return cleaned


def clamp_score(value):
    try:
        score = int(value)
    except Exception:
        return 3
    return max(1, min(5, score))


def compact_item_for_prompt(item, idx):
    """
    Builds the per-item evidence packet sent to the LLM.
    """
    text = item.get("extracted_text") or item.get("summary") or item.get("raw_summary") or ""
    return {
        "temp_id": idx,
        "title": item.get("title") or item.get("extracted_title") or "",
        "institution": item.get("institution", ""),
        "url": item.get("canonical_url") or item.get("url", ""),
        "date": item.get("date", ""),
        "date_status": item.get("date_status", "not_checked"),
        "date_confidence": item.get("date_confidence", "unknown"),
        "first_seen_run_date": item.get("first_seen_run_date", ""),
        "seen_status": item.get("seen_status", ""),
        "first_verified_publication_date": item.get("first_verified_publication_date", ""),
        "last_verified_publication_date": item.get("last_verified_publication_date", ""),
        "date_crosscheck_status": item.get("date_crosscheck_status", ""),
        "date_crosscheck_note": item.get("date_crosscheck_note", ""),
        "last_reported_run_date": item.get("last_reported_run_date", ""),
        "author": item.get("author") or item.get("extracted_author") or "",
        "content_type_guess": item.get("content_type_guess") or item.get("item_type") or "report",
        "extraction_status": item.get("extraction_status", "not_enriched"),
        "extracted_text_chars": item.get("extracted_text_chars", 0),
        "discovery_methods": item.get("discovery_methods") or [item.get("discovery_method", "unknown")],
        "excerpt_is_partial": len(text) > config.LLM_ITEM_TEXT_CHAR_LIMIT,
        "source_summary": (item.get("summary") or item.get("raw_summary") or "")[:1000] if item.get("rescue_status") == "recovered" else item.get("summary") or item.get("raw_summary") or "",
        "full_text_excerpt": evidence_selection.excerpt(text, budget=min(config.LLM_ITEM_TEXT_CHAR_LIMIT, config.RESCUE_TEXT_CHAR_LIMIT) if item.get("rescue_status") == "recovered" else None),
        "rescue_evidence_url": item.get("rescue_evidence_url", ""),
        "evidence_quality": item.get("evidence_quality", "unknown"),
        "event_start_at": item.get("event_start_at", ""),
    }


def build_analysis_prompt(batch):
    topic_block = topic_utils.build_topic_prompt_block()
    batch_input = [compact_item_for_prompt(item, idx) for idx, item in enumerate(batch)]
    return f"""
You are a senior economic-security analyst. You are reviewing candidate think-tank outputs that have already been discovered broadly. Your task is to make a careful second-stage relevance decision.

Topic ontology. Choose matched_topics ONLY from this list, using the include/exclude guidance:
{topic_block}

Candidate evidence packets:
{json.dumps(batch_input, indent=2)}

For EACH candidate, decide whether it is materially relevant to at least one topic. Use the full_text_excerpt when it is available.

Rules:
1. Prefer recall when the evidence clearly relates to the ontology, but reject passing mentions, generic geopolitics, routine macro commentary, and items with no material economic-security angle.
2. If evidence is insufficient to establish relevance, set needs_review to true rather than guessing or rejecting from a title alone.
3. Never override publication dates or prior-report history. Modification timestamps are not publication dates. Only events with a verified future event_start_at are eligible outside the publication window.
4. Undated publications may be assessed for relevance, but are held for review and cannot appear as new publications. Do not invent dates.
5. First-seen dates are discovery timestamps, not publication dates. Do not treat an unresolved date as a relevance rejection; date eligibility is enforced separately.
6. {editorial_policy.POLICY}
7. Reader presentation: write a clear, informative title of roughly 8-12 words, preserving the article's meaning. Write why_it_matters as one direct sentence, ideally at most 35 words, stating the strategic economic implication. Write the summary as exactly two complete sentences, ideally 55-75 words, covering the finding and mechanism without repeating the title or why_it_matters. These are presentation requirements, not additional relevance criteria. Ground every claim in the evidence packet; do not invent details, exaggerate significance or imply a think tank's proposal is an established fact.

Return raw JSON only, with exactly one analysis for every input item. Copy each input temp_id into its result. Order is not significant:
{{
  "analyses": [
    {{
      "temp_id": 0,
      "needs_review": false,
      "is_material_match": true,
      "title": "Cleaned title",
      "author": "Author(s) or speakers, or N/A",
      "matched_topics": ["Topic 1"],
      "editorial_tier": "main",
      "coverage_relationship": "central",
      "topic_evidence": [{{"topic": "Topic 1", "role": "central", "mechanism": "Explain the specific economic mechanism", "quotes": ["Verbatim body quote at least 25 characters"]}}],
      "summary": "2-4 sentence evidence-grounded summary.",
      "why_it_matters": "1-2 sentence strategic relevance statement.",
      "importance_score": 3,
      "category": "report",
      "event_time": null,
      "relevance_confidence": "high",
      "evidence": ["Verbatim body quote of at least 25 characters"],
      "exclusion_reason": "",
      "date_note": ""
    }}
  ]
}}
"""


def make_final_item(orig_item, analysis):
    topics = valid_topics(analysis.get("matched_topics") or [])

    final_item = {
        "title": analysis.get("title") or orig_item.get("title") or orig_item.get("extracted_title"),
        "institution": orig_item.get("institution"),
        "date": orig_item.get("date") or orig_item.get("extracted_date") or "",
        "author": analysis.get("author") or orig_item.get("author") or orig_item.get("extracted_author") or "N/A",
        "tags": topics,
        "editorial_tier": analysis["editorial_tier"],
        "coverage_relationship": analysis["coverage_relationship"],
        "topic_evidence": analysis["topic_evidence"],
        "summary": analysis.get("summary") or orig_item.get("summary") or "",
        "why_it_matters": analysis.get("why_it_matters") or "",
        "importance_score": clamp_score(analysis.get("importance_score")),
        "url": orig_item.get("canonical_url") or orig_item.get("url"),
        "relevance_confidence": analysis.get("relevance_confidence") or "medium",
        "evidence": analysis.get("evidence") or [],
        "date_status": orig_item.get("date_status", "not_checked"),
        "date_confidence": orig_item.get("date_confidence", "unknown"),
        "date_note": analysis.get("date_note") or "",
        "seen_item_key": orig_item.get("seen_item_key", ""),
        "seen_status": orig_item.get("seen_status", ""),
        "first_seen_run_date": orig_item.get("first_seen_run_date", ""),
        "last_seen_run_date": orig_item.get("last_seen_run_date", ""),
        "first_verified_publication_date": orig_item.get("first_verified_publication_date", ""),
        "last_verified_publication_date": orig_item.get("last_verified_publication_date", ""),
        "date_crosscheck_status": orig_item.get("date_crosscheck_status", ""),
        "date_crosscheck_note": orig_item.get("date_crosscheck_note", ""),
        "last_reported_run_date": orig_item.get("last_reported_run_date", ""),
        "extraction_status": orig_item.get("extraction_status", "not_enriched"),
        "extracted_text_chars": orig_item.get("extracted_text_chars", 0),
        "discovery_methods": orig_item.get("discovery_methods") or [orig_item.get("discovery_method", "unknown")],
        "topic_hints": orig_item.get("topic_hints", []),
    }
    for field in ("scan_run_id", "scan_run_date", "date_source", "published_at_verified", "modified_at", "modified_date_source", "event_start_at", "event_end_at", "event_start_precision", "is_listing_page", "item_type", "content_type_guess", "last_emailed_run_date", "resolved_url", "canonical_url", "evidence_quality", "publication_date_source_detail", "decision_cache_status"):
        if field in orig_item:
            final_item[field] = orig_item[field]
    if analysis.get("event_time"):
        final_item["event_time"] = analysis["event_time"]
    for field in ("rescue_status", "rescue_evidence_url"):
        if field in orig_item:
            final_item[field] = orig_item[field]
    return final_item


def make_exclusion_item(orig_item, analysis, reason_prefix=""):
    reason = analysis.get("exclusion_reason") or ("Insufficient evidence to establish relevance." if analysis.get("needs_review") else "Not a material match to the configured economic-security topic set.")
    if reason_prefix:
        reason = f"{reason_prefix}: {reason}"
    return {
        "title": orig_item.get("title") or orig_item.get("extracted_title") or "",
        "institution": orig_item.get("institution", ""),
        "url": orig_item.get("canonical_url") or orig_item.get("url", ""),
        "date": orig_item.get("date", ""),
        "date_status": orig_item.get("date_status", "not_checked"),
        "seen_item_key": orig_item.get("seen_item_key", ""),
        "seen_status": orig_item.get("seen_status", ""),
        "first_seen_run_date": orig_item.get("first_seen_run_date", ""),
        "first_verified_publication_date": orig_item.get("first_verified_publication_date", ""),
        "date_crosscheck_status": orig_item.get("date_crosscheck_status", ""),
        "date_crosscheck_note": orig_item.get("date_crosscheck_note", ""),
        "last_reported_run_date": orig_item.get("last_reported_run_date", ""),
        "extraction_status": orig_item.get("extraction_status", "not_enriched"),
        "topic_hints": orig_item.get("topic_hints", []),
        "discovery_methods": orig_item.get("discovery_methods") or [orig_item.get("discovery_method", "unknown")],
        "relevance_confidence": analysis.get("relevance_confidence") or "low",
        "exclusion_reason": reason,
        "evidence": analysis.get("evidence") or [],
    }


def add_item_to_category(analyzed_data, item, category):
    checked = dict(item)
    if category == "event":
        checked["item_type"] = "event"
    blocked = eligibility.exclusion_reason(checked, require_publication=True)
    if blocked:
        analyzed_data.setdefault("needs_review", []).append(dict(item, exclusion_reason=blocked))
        return False
    if category == "event":
        analyzed_data["events"].append(item)
    elif category == "podcast":
        analyzed_data["podcasts"].append(item)
    else:
        analyzed_data["reports"].append(item)
    return True


def analyze_without_api(items):
    """Compatibility entry point: missing review cannot produce inclusions."""
    data = {key: [] for key in ("reports", "events", "podcasts", "excluded", "needs_review")}
    for item in deduplicate_items(items):
        data["needs_review"].append(make_exclusion_item(item, {
            "needs_review": True, "exclusion_reason": "model_review_failed: no API key configured"}))
    return data


def analyze_items(items, override_model=None):
    """Review sufficient evidence; cache decisions and match responses by ID."""
    model = ai_client.get_openrouter_model(override_model)
    run = scan_runtime.current()
    request_start = len(getattr(run, "model_requests", []))
    unique_items = deduplicate_items(topic_utils.annotate_topic_hints(items))
    data = {key: [] for key in ("reports", "events", "podcasts", "excluded", "needs_review")}
    data["validation_failures"] = []
    data["editorial_normalizations"] = []
    metrics = {"selected_candidates": len(items), "unique_candidates": len(unique_items),
               "cache_hits": 0, "sent_to_model": 0, "insufficient_evidence": 0, "individual_recoveries": 0, "repair_attempts": 0}
    uncached = []

    def apply(item, analysis):
        if analysis.get("needs_review"):
            data["needs_review"].append(make_exclusion_item(item, analysis, "insufficient_evidence"))
            return
        if not analysis["is_material_match"]:
            data["excluded"].append(make_exclusion_item(item, analysis))
            return
        category = analysis.get("category") or item.get("item_type") or "report"
        category = category if category in ("report", "event", "podcast") else "report"
        add_item_to_category(data, make_final_item(item, analysis), category)

    for item in unique_items:
        if item.get("evidence_quality") == "insufficient" or not str(item.get("extracted_text") or "").strip() or item.get("paywall_detected"):
            data["needs_review"].append(make_exclusion_item(item, {"exclusion_reason": "insufficient_evidence"}))
            metrics["insufficient_evidence"] += 1
            continue
        cached = run.get_decision(item, model) if run else None
        if cached and not editorial_policy.validation_error(item, cached):
            item["decision_cache_status"] = "hit"
            metrics["cache_hits"] += 1
            apply(item, cached)
        else:
            uncached.append(item)

    if not config.OPENROUTER_API_KEY:
        for item in uncached:
            data["needs_review"].append(make_exclusion_item(item, {"exclusion_reason": "model_review_failed: no API key configured"}))
        data["analysis_metrics"] = metrics
        return data

    batch_size = max(1, config.LLM_BATCH_SIZE)
    ordinary = [item for item in uncached if item.get("rescue_status") != "recovered"]
    rescued = [item for item in uncached if item.get("rescue_status") == "recovered"]
    metrics["rescued_sent_to_model"] = len(rescued)
    batches = [ordinary[i:i + batch_size] for i in range(0, len(ordinary), batch_size)] + [[item] for item in rescued]
    def request(batch, purpose="relevance", repair_error=""):
        prompt = build_analysis_prompt(batch)
        if repair_error:
            prompt += "\nYour previous decision failed validation: " + repair_error + ". Reassess using only the supplied body; drop unsupported topics or set needs_review."
        return ai_client.generate_json_with_retry(model=model,
            messages=[{"role": "user", "content": prompt}], max_retries=1 if repair_error else 2,
            max_tokens=1600 if purpose == "evidence_rescue" else max(2400, len(batch) * 2000),
            purpose=purpose, response_schema=editorial_policy.response_schema())

    def rows_by_id(result, batch):
        rows = result.get("analyses", []) if isinstance(result, dict) else []
        by_id, duplicate = {}, set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            index = row.get("temp_id")
            if type(index) is not int or not 0 <= index < len(batch):
                continue
            if index in by_id:
                duplicate.add(index)
            by_id[index] = row
        return {i: row for i, row in by_id.items() if i not in duplicate}

    def finish(item, row):
        prepared = editorial_policy.derive_editorial_fields(row, item)
        if prepared != row:
            data["editorial_normalizations"].append({"url": item.get("url"), "model_decision": row,
                "reason": "Derived section and article evidence from topic roles; discarded unsupported individual quotes. Original model fields retained here."})
        row = prepared
        error = editorial_policy.validation_error(item, row)
        if error:
            data["validation_failures"].append({"url": item.get("url"), "stage": "initial", "error": error, "decision": row})
            metrics["repair_attempts"] += 1
            try:
                row = rows_by_id(request([item], "schema_repair", error), [item]).get(0)
                row = editorial_policy.derive_editorial_fields(row, item)
                error = editorial_policy.validation_error(item, row)
            except Exception as exc:
                error = str(exc)[:160]
            if error:
                data["validation_failures"].append({"url": item.get("url"), "stage": "repair", "error": error, "decision": row})
                data["needs_review"].append(make_exclusion_item(item, {"exclusion_reason": "model_response_invalid: " + error}))
                return
        item["decision_cache_status"] = "miss"
        if run and not row.get("needs_review"):
            run.cache_decision(item, model, row)
        apply(item, row)

    for batch_index, batch in enumerate(batches):
        print(f"[*] Reviewing relevance batch {batch_index + 1}/{len(batches)} ({len(batch)} candidates)...")
        metrics["sent_to_model"] += len(batch)
        purpose = "evidence_rescue" if batch[0].get("rescue_status") == "recovered" else "relevance"
        try:
            by_id = rows_by_id(request(batch, purpose), batch)
        except Exception as exc:
            if any(code in str(exc) for code in ("OpenRouter HTTP 400", "OpenRouter HTTP 401", "OpenRouter HTTP 402", "OpenRouter HTTP 403")):
                # Splitting cannot repair invalid credentials, unavailable
                # credit or an unsupported request schema.
                for item in batch:
                    data["needs_review"].append(make_exclusion_item(item, {"exclusion_reason": "model_review_failed: " + str(exc)[:160]}))
                continue
            if len(batch) == 1:
                data["needs_review"].append(make_exclusion_item(batch[0], {"exclusion_reason": "model_review_failed: " + str(exc)[:160]}))
                continue
            # Recover each item independently after a transport/batch failure.
            for item in batch:
                try:
                    result = rows_by_id(request([item], "individual_recovery"), [item])
                    finish(item, result.get(0))
                    metrics["individual_recoveries"] += 1
                except Exception as individual_exc:
                    data["needs_review"].append(make_exclusion_item(item, {"exclusion_reason": "model_review_failed: " + str(individual_exc)[:160]}))
            if run:
                run.save_manifest("running")
            continue
        for index, item in enumerate(batch):
            finish(item, by_id.get(index))
        if run:
            run.save_manifest("running")
        if batch_index + 1 < len(batches):
            time.sleep(2)
    for category in ("reports", "events", "podcasts"):
        data[category].sort(key=lambda row: (row.get("importance_score", 0), row.get("date", "")), reverse=True)
    assert sum(len(data[k]) for k in ("reports", "events", "podcasts", "excluded", "needs_review")) == len(unique_items), "Analysis totals do not reconcile"
    rescue_requests = [row for row in getattr(run, "model_requests", [])[request_start:]
                       if row.get("purpose") == "evidence_rescue"]
    metrics["rescue_usage"] = {
        "requests_including_retries": len(rescue_requests),
        "requests_with_usage": sum(isinstance(row.get("usage"), dict) for row in rescue_requests),
        "reported_prompt_tokens": sum((row.get("usage") or {}).get("prompt_tokens", 0) or 0 for row in rescue_requests),
        "reported_completion_tokens": sum((row.get("usage") or {}).get("completion_tokens", 0) or 0 for row in rescue_requests),
        "reported_cost_usd": sum((row.get("usage") or {}).get("cost", 0) or 0 for row in rescue_requests),
    }
    data["analysis_metrics"] = metrics
    return data
