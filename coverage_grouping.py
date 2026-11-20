"""Group closely related publications in a reader-facing edition."""
import re
from datetime import datetime
from urllib.parse import urlparse


def publication_day(item):
    try:
        return datetime.strptime(item.get("date") or "", "%d %B %Y").date()
    except ValueError:
        return None


def score(item):
    try:
        return int(item.get("importance_score") or 0)
    except (TypeError, ValueError):
        return 0


def preferred_order(item):
    return (item.get("editorial_tier") == "further_reading", -score(item))


def title_words(title):
    return set(re.findall(r"[a-z0-9]+", title.casefold())) - {
        "a", "an", "and", "as", "by", "for", "in", "of", "on", "the", "to", "with",
    }


def group_publications(publications):
    ordered = sorted(publications, key=preferred_order)
    overview_days = {
        publication_day(item)
        for item in ordered
        if item.get("institution") == "Council on Foreign Relations (CFR)"
        and "report series" in item.get("title", "").casefold()
        and "western hemisphere" in item.get("title", "").casefold()
    } - {None}
    groups = []
    for item in ordered:
        item = dict(item, supporting_publications=[])
        title = item.get("title") or ""
        words = title_words(title)
        day = publication_day(item)
        source = item.get("institution")
        path = urlparse(item.get("url") or "").path.lower()
        series = ""
        if source == "Council on Foreign Relations (CFR)" and day in overview_days:
            if re.search(r"western hemisphere|americas|greater north america|venezuela transition", title, re.I):
                series = "CFR Western Hemisphere strategy series"
        if source == "Institute for the Study of War (ISW)" and "iran-update-" in path:
            if "hormuz" in words and words & {"shipping", "oil", "energy"}:
                series = "Strait of Hormuz shipping and energy disruption"
        for lead, lead_day, lead_words, lead_series in groups:
            if not day or not lead_day or not item.get("url") or item["url"] == lead.get("url"):
                continue
            days_apart = abs((day - lead_day).days)
            max_series_gap = 0 if source == "Council on Foreign Relations (CFR)" else 2
            same_series = series and series == lead_series and days_apart <= max_series_gap
            near_duplicate = (
                days_apart <= 2 and source and source == lead.get("institution")
                and len(words & lead_words) >= 6
                and len(words & lead_words) / len(words | lead_words) >= 0.8
            )
            if same_series or near_duplicate:
                lead["supporting_publications"].append(item)
                if same_series:
                    lead["coverage_group_title"] = series
                break
        else:
            groups.append((item, day, words, series))
    return [lead for lead, *_ in groups]
