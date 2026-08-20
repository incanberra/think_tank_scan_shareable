import re
from collections import defaultdict

import config


def normalize_text(value):
    """
    Collapses text to a lowercase searchable string.
    """
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def build_topic_prompt_block():
    """
    Returns a compact topic guide for the relevance-review prompt.
    """
    blocks = []
    for topic in config.ECONOMIC_SECURITY_TOPICS:
        entry = config.TOPIC_ONTOLOGY.get(topic, {})
        keywords = ", ".join(entry.get("keywords", [])[:12])
        include = entry.get("include", "")
        exclude = entry.get("exclude", "")
        blocks.append(
            f"- {topic}\n"
            f"  Include: {include}\n"
            f"  Exclude: {exclude}\n"
            f"  Signals: {keywords}"
        )
    return "\n".join(blocks)


def find_topic_hints(item, text_limit=8000):
    """
    Performs a broad keyword pass to avoid starving the LLM of plausible topics.
    This is intentionally recall-oriented; the LLM still makes the final call.
    """
    searchable_parts = [
        item.get("title", ""),
        item.get("extracted_title", ""),
        item.get("summary", ""),
        item.get("raw_summary", ""),
        item.get("author", ""),
        " ".join(item.get("tags", []) or []),
        item.get("extracted_text", "")[:text_limit],
    ]
    text = normalize_text(" ".join(searchable_parts))
    if not text:
        return []

    hints = []
    for topic, entry in config.TOPIC_ONTOLOGY.items():
        matches = []
        for keyword in entry.get("keywords", []):
            keyword_norm = normalize_text(keyword)
            if not keyword_norm:
                continue
            pattern = r"\b" + re.escape(keyword_norm).replace(r"\ ", r"\s+") + r"\b"
            if re.search(pattern, text):
                matches.append(keyword)
        if matches:
            hints.append(
                {
                    "topic": topic,
                    "matched_keywords": sorted(set(matches)),
                    "score": len(set(matches)),
                }
            )

    hints.sort(key=lambda row: row["score"], reverse=True)
    return hints


def annotate_topic_hints(items):
    """
    Adds topic_hints to each candidate item and returns the same list.
    """
    for item in items:
        item["topic_hints"] = find_topic_hints(item)
    return items


def summarize_topic_hints(items):
    """
    Summarizes broad-match coverage for recall auditing.
    """
    counts = defaultdict(int)
    no_hint = 0
    for item in items:
        hints = item.get("topic_hints") or []
        if not hints:
            no_hint += 1
            continue
        for hint in hints:
            counts[hint["topic"]] += 1
    return {
        "items_with_no_topic_hint": no_hint,
        "topic_hint_counts": dict(sorted(counts.items())),
    }
