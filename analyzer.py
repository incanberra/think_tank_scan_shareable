import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import ai_client
import config
import topic_utils

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_url(url):
    """
    Normalizes URLs for deduplication without destroying meaningful paths.
    """
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
    clean_query = urlencode(query_pairs)
    path = parsed.path.rstrip("/") or parsed.path
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            clean_query,
            "",
        )
    )


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


def fallback_tags(item):
    hint_topics = [hint["topic"] for hint in item.get("topic_hints", [])]
    return valid_topics(item.get("tags") or hint_topics) or hint_topics[:2]


def compact_item_for_prompt(item, idx):
    """
    Builds the per-item evidence packet sent to the LLM.
    """
    text = item.get("extracted_text") or item.get("summary") or item.get("raw_summary") or ""
    topic_hints = [
        {
            "topic": hint.get("topic"),
            "matched_keywords": hint.get("matched_keywords", [])[:8],
        }
        for hint in (item.get("topic_hints") or [])[:5]
    ]
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
        "topic_hints": topic_hints,
        "source_summary": item.get("summary") or item.get("raw_summary") or "",
        "full_text_excerpt": text[: config.LLM_ITEM_TEXT_CHAR_LIMIT],
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

For EACH candidate, decide whether it is materially relevant to at least one topic. Use the full_text_excerpt when it is available. Topic hints are recall aids, not final judgments.

Rules:
1. Prefer recall when the evidence clearly relates to the ontology, but reject passing mentions, generic geopolitics, routine macro commentary, and items with no material economic-security angle.
2. If extraction_status is not "ok", judge from available metadata but set relevance_confidence to "low" unless the relevance is obvious.
3. If date_status is "verified_out_of_window", usually reject it for the daily report unless it is an upcoming event or the evidence shows the relevant publication/announcement date is actually in scope.
4. If date_status is "date_unknown", do not reject only for that reason; flag the uncertainty in date_note.
5. Use first_seen_run_date and date_crosscheck_status as a date sanity check. If an item has date_unknown_seen_before, treat it as probably stale unless other evidence shows a fresh publication, update, or upcoming event.
6. Every included item must have 1-3 short evidence strings copied or closely paraphrased from the title, summary, or full text. Evidence should explain why it matched the topic set.
7. Summaries must be 2-4 sentences and grounded in the evidence packet. Do not invent details that are not supported by the packet.

Return raw JSON only, with exactly one analysis for every input item in the same order:
{{
  "analyses": [
    {{
      "is_material_match": true,
      "title": "Cleaned title",
      "author": "Author(s) or speakers, or N/A",
      "matched_topics": ["Topic 1"],
      "summary": "2-4 sentence evidence-grounded summary.",
      "why_it_matters": "1-2 sentence strategic relevance statement.",
      "importance_score": 3,
      "category": "report",
      "event_time": null,
      "relevance_confidence": "high",
      "evidence": ["Short evidence point 1", "Short evidence point 2"],
      "exclusion_reason": "",
      "date_note": ""
    }}
  ]
}}
"""


def make_final_item(orig_item, analysis):
    topics = valid_topics(analysis.get("matched_topics") or [])
    if not topics:
        topics = fallback_tags(orig_item)

    final_item = {
        "title": analysis.get("title") or orig_item.get("title") or orig_item.get("extracted_title"),
        "institution": orig_item.get("institution"),
        "date": orig_item.get("date") or orig_item.get("extracted_date") or "",
        "author": analysis.get("author") or orig_item.get("author") or orig_item.get("extracted_author") or "N/A",
        "tags": topics,
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
    if analysis.get("event_time"):
        final_item["event_time"] = analysis["event_time"]
    return final_item


def make_exclusion_item(orig_item, analysis, reason_prefix=""):
    reason = analysis.get("exclusion_reason") or "Not a material match to the configured economic-security topic set."
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
    if category == "event":
        analyzed_data["events"].append(item)
    elif category == "podcast":
        analyzed_data["podcasts"].append(item)
    else:
        analyzed_data["reports"].append(item)


def analyze_without_api(items):
    print("[!] Warning: OPENROUTER_API_KEY not set. Keeping candidates as unreviewed recall items.")
    analyzed_items = {
        "reports": [],
        "events": [],
        "podcasts": [],
        "excluded": [],
        "needs_review": [],
    }
    for item in deduplicate_items(topic_utils.annotate_topic_hints(items)):
        fallback_item = dict(item)
        fallback_item["importance_score"] = fallback_item.get("importance_score", 3)
        fallback_item["why_it_matters"] = "No API key configured for evidence-backed relevance review."
        fallback_item["tags"] = fallback_tags(fallback_item)
        fallback_item["relevance_confidence"] = "unreviewed"
        fallback_item["evidence"] = [fallback_item.get("candidate_reason") or fallback_item.get("summary", "")[:200]]
        category = fallback_item.get("item_type") or fallback_item.get("content_type_guess") or "report"
        if fallback_item.get("date_status") == "verified_out_of_window" and category != "event":
            analyzed_items["needs_review"].append(
                make_exclusion_item(fallback_item, {"exclusion_reason": "Relevant candidate may be outside coverage window."})
            )
        else:
            add_item_to_category(analyzed_items, fallback_item, category)
    return analyzed_items


def analyze_items(items, override_model=None):
    """
    Performs evidence-backed LLM relevance review over enriched candidates.
    """
    if not config.OPENROUTER_API_KEY:
        return analyze_without_api(items)

    model = ai_client.get_openrouter_model(override_model)
    unique_items = deduplicate_items(topic_utils.annotate_topic_hints(items))
    print(f"[*] Deduplicated candidate items from {len(items)} down to {len(unique_items)}.")

    analyzed_data = {
        "reports": [],
        "events": [],
        "podcasts": [],
        "excluded": [],
        "needs_review": [],
    }

    if not unique_items:
        return analyzed_data

    batch_size = max(1, config.LLM_BATCH_SIZE)
    batches = [unique_items[i : i + batch_size] for i in range(0, len(unique_items), batch_size)]

    for batch_idx, batch in enumerate(batches):
        print(f"[*] Reviewing relevance batch {batch_idx + 1}/{len(batches)} (contains {len(batch)} items)...")
        prompt = build_analysis_prompt(batch)

        try:
            print(f"    [+] Querying OpenRouter model: {model}...")
            result_data = ai_client.generate_json_with_retry(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            analyses = result_data.get("analyses", []) if isinstance(result_data, dict) else result_data
            if isinstance(analyses, dict):
                analyses = [analyses]
            if not isinstance(analyses, list):
                analyses = []

            included = 0
            excluded = 0
            review = 0

            for idx, orig_item in enumerate(batch):
                if idx >= len(analyses) or not isinstance(analyses[idx], dict):
                    analyzed_data["needs_review"].append(
                        make_exclusion_item(
                            orig_item,
                            {"exclusion_reason": "Model did not return a parseable decision for this candidate."},
                        )
                    )
                    review += 1
                    continue

                analysis = analyses[idx]
                category = analysis.get("category") or orig_item.get("content_type_guess") or orig_item.get("item_type") or "report"
                if category not in ["report", "event", "podcast"]:
                    category = "report"

                if analysis.get("is_material_match", False):
                    final_item = make_final_item(orig_item, analysis)
                    if final_item.get("date_status") == "verified_out_of_window" and category != "event":
                        analyzed_data["needs_review"].append(
                            make_exclusion_item(
                                orig_item,
                                analysis,
                                reason_prefix="Material match but outside coverage window",
                            )
                        )
                        review += 1
                    else:
                        add_item_to_category(analyzed_data, final_item, category)
                        included += 1
                else:
                    analyzed_data["excluded"].append(make_exclusion_item(orig_item, analysis))
                    excluded += 1

            print(f"    [+] Batch complete: {included} included, {excluded} excluded, {review} queued for review.")

            if batch_idx < len(batches) - 1:
                print("    [*] Spacing delay: sleeping 2 seconds before next batch...")
                time.sleep(2)

        except Exception as exc:
            print(f"    [-] Failed to analyze batch {batch_idx + 1}: {exc}")
            for item in batch:
                analyzed_data["needs_review"].append(
                    make_exclusion_item(
                        item,
                        {"exclusion_reason": f"Batch review failed: {str(exc)[:160]}"},
                    )
                )

    for key in ["reports", "events", "podcasts"]:
        analyzed_data[key].sort(key=lambda row: (row.get("importance_score", 0), row.get("date", "")), reverse=True)

    return analyzed_data
