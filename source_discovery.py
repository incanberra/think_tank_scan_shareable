import re
import time
from datetime import datetime
from urllib.parse import parse_qsl, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

import config
import content_extractor

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

COMMON_SITEMAP_PATHS = [
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemap-news.xml",
    "/news-sitemap.xml",
    "/post-sitemap.xml",
    "/page-sitemap.xml",
]

CONTENT_PATH_HINTS = [
    "/paper",
    "/papers",
    "/publication",
    "/publications",
    "/research",
    "/analysis",
    "/commentary",
    "/article",
    "/articles",
    "/report",
    "/reports",
    "/event",
    "/events",
    "/podcast",
    "/podcasts",
    "/video",
    "/videos",
    "/digest",
    "/expert-speak",
    "/online-analysis",
]

GENERIC_LISTING_PATHS = {
    "/analysis",
    "/article",
    "/articles",
    "/commentary",
    "/event",
    "/events",
    "/expert-speak",
    "/online-analysis",
    "/podcast",
    "/podcasts",
    "/paper",
    "/papers",
    "/publication",
    "/publications",
    "/report",
    "/reports",
    "/research",
    "/video",
    "/videos",
    "/digest",
}

NOISY_PATH_TOKENS = [
    "/archive",
    "/archives",
    "/author",
    "/authors",
    "/category",
    "/categories",
    "/tag",
    "/tags",
    "/topic",
    "/topics",
    "/sitemap",
    "/robots.txt",
]

NOISY_PATH_SUFFIXES = {
    "/articles-multimedia",
    "/briefings-booktalks-and-conversations",
    "/calendar",
    "/chain-reaction",
    "/commentary",
    "/congressional-testimony",
    "/conversations",
    "/defense-discussions",
    "/expert-commentary",
    "/insights-papers",
    "/intern-corner",
    "/podcasts",
    "/policy-minded",
    "/publications/search",
    "/research-groups-experts",
    "/research-integrity",
    "/research-papers",
    "/rusi-books",
    "/rusi-defence-systems",
    "/rusi-journal",
    "/rusi-newsbrief",
    "/the-gaming-lab",
    "/toolkits",
    "/whitehall-papers",
    "/writepeace-blog",
}

NOISY_TITLE_EXACT = {
    "analysis",
    "all events",
    "articles & multimedia",
    "armament and disarmament",
    "articles",
    "briefings, booktalks, and conversations",
    "calendar",
    "chain reaction",
    "commentary",
    "congressional testimony",
    "current page",
    "current page 1",
    "defense discussions",
    "events",
    "expert commentary",
    "first page",
    "insights papers",
    "intern corner",
    "last page",
    "learn more",
    "map room",
    "next page",
    "policy minded: rand's flagship podcast",
    "publications search",
    "podcasts",
    "previous page",
    "publications",
    "read more essays",
    "reports",
    "research",
    "research & analysis",
    "research groups & experts",
    "research integrity",
    "research papers",
    "rusi books",
    "rusi defence systems",
    "rusi journal",
    "rusi newsbrief",
    "skip to content",
    "the gaming lab",
    "toolkits",
    "view chevron-right",
    "videos",
    "whitehall papers",
    "%year_event%",
}

NOISY_TITLE_PREFIXES = (
    "current page ",
    "go to page ",
    "page ",
)


def source_base_url(source):
    return source.get("base_url") or f"https://www.{source['domain']}/"


def fetch_text(url, timeout=20):
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
    except Exception as exc:
        return None, f"fetch_failed ({str(exc)[:80]})"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    return response.text, "ok"


def is_same_domain(url, domain):
    host = urlparse(url).netloc.lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def looks_like_content_url(url):
    path = urlparse(url).path.lower()
    return any(hint in path for hint in CONTENT_PATH_HINTS)


def native_rejection_reason(url, title="", allow_dated_listing=False):
    parsed = urlparse(url)
    path = (parsed.path or "/").lower().rstrip("/") or "/"
    title_key = re.sub(r"\s+", " ", str(title or "").strip().lower())
    query_keys = {key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}

    if path.endswith(".xml") or "sitemap" in path:
        return "native_rejected_sitemap_xml"
    if path in NOISY_PATH_SUFFIXES or any(path.endswith(suffix) for suffix in NOISY_PATH_SUFFIXES):
        return "native_rejected_listing_page"
    if path.endswith("/robots.txt") or any(token in path for token in NOISY_PATH_TOKENS):
        return "native_rejected_non_content_path"
    if query_keys and query_keys.issubset({"page", "paged", "p"}):
        return "native_rejected_pagination_url"
    if title_key in NOISY_TITLE_EXACT or any(title_key.startswith(prefix) for prefix in NOISY_TITLE_PREFIXES):
        return "native_rejected_navigation_title"
    if not allow_dated_listing and path in GENERIC_LISTING_PATHS:
        return "native_rejected_listing_page"
    return ""


def rejection_item(source, url, method, reason, title="", query=""):
    return {
        "title": clean_title(title) or url.rstrip("/").split("/")[-1].replace("-", " ").title(),
        "institution": source["name"],
        "url": url.split("#")[0],
        "source_domain": source["domain"],
        "discovery_method": method,
        "discovery_methods": [method],
        "discovery_query": query,
        "discovery_queries": [query] if query else [],
        "native_rejection_reason": reason,
    }


def infer_item_type(url):
    path = urlparse(url).path.lower()
    if "/event" in path or "/events" in path:
        return "event"
    if "/podcast" in path or "/video" in path:
        return "podcast"
    return "report"


def clean_title(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    value = re.sub(r"\s+[|-]\s+.*$", "", value)
    return value


def candidate_from_url(source, url, method, title="", summary="", date_value="", query=""):
    parsed_dt = content_extractor.parse_date(date_value)
    display_date = content_extractor.format_display_date(parsed_dt) if parsed_dt else ""
    return {
        "title": clean_title(title) or url.rstrip("/").split("/")[-1].replace("-", " ").title(),
        "institution": source["name"],
        "date": display_date,
        "author": "",
        "summary": summary,
        "raw_summary": summary,
        "url": url.split("#")[0],
        "item_type": infer_item_type(url),
        "source_domain": source["domain"],
        "published_at": parsed_dt,
        "discovery_method": method,
        "discovery_methods": [method],
        "discovery_query": query,
        "discovery_queries": [query] if query else [],
        "candidate_reason": f"Found through source-native {method} discovery.",
    }


def discover_sitemap_urls(source):
    base = source_base_url(source)
    candidates = set(source.get("sitemap_urls", []))
    for path in COMMON_SITEMAP_PATHS:
        candidates.add(urljoin(base, path))

    robots_url = urljoin(base, "/robots.txt")
    robots_text, status = fetch_text(robots_url, timeout=12)
    if robots_text:
        for line in robots_text.splitlines():
            if line.lower().startswith("sitemap:"):
                candidates.add(line.split(":", 1)[1].strip())

    return sorted(candidates), status


def parse_sitemap(text):
    soup = BeautifulSoup(text, "xml")
    nested = []
    urls = []

    for sitemap in soup.find_all("sitemap"):
        loc = sitemap.find("loc")
        if loc and loc.text.strip():
            nested.append(loc.text.strip())

    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        if not loc or not loc.text.strip():
            continue
        lastmod = url_tag.find("lastmod")
        urls.append(
            {
                "url": loc.text.strip(),
                "lastmod": lastmod.text.strip() if lastmod and lastmod.text else "",
            }
        )

    return nested, urls


def sitemap_url_relevant(sitemap_url):
    text = sitemap_url.lower()
    relevant_tokens = [
        "post",
        "article",
        "publication",
        "research",
        "event",
        "news",
        "page",
        "report",
        "analysis",
    ]
    noisy_tokens = ["image", "video", "attachment", "author", "tag", "category"]
    if any(token in text for token in noisy_tokens) and not any(token in text for token in relevant_tokens):
        return False
    return True


def scan_sitemaps(source, run_date_str, coverage_hours=48, max_nested=20, max_urls=2500):
    start_dt, end_dt = content_extractor.coverage_window(run_date_str, coverage_hours)
    sitemap_urls, robots_status = discover_sitemap_urls(source)
    seen_sitemaps = set()
    queue = [url for url in sitemap_urls if sitemap_url_relevant(url)]
    raw_candidates = {}
    rejected = []
    checked = 0
    failures = []

    while queue and checked < max_nested:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        text, status = fetch_text(sitemap_url, timeout=20)
        checked += 1
        if not text:
            failures.append(f"{sitemap_url} ({status})")
            continue

        nested, urls = parse_sitemap(text)
        for nested_url in nested:
            if nested_url not in seen_sitemaps and sitemap_url_relevant(nested_url):
                queue.append(nested_url)

        for entry in urls[:max_urls]:
            url = entry["url"]
            if not is_same_domain(url, source["domain"]) or not looks_like_content_url(url):
                continue

            lastmod_dt = content_extractor.parse_date(entry.get("lastmod"))
            rejection_reason = native_rejection_reason(
                url,
                allow_dated_listing=bool(lastmod_dt),
            )
            if rejection_reason:
                rejected.append(rejection_item(source, url, "sitemap", rejection_reason, query=sitemap_url))
                continue
            if lastmod_dt:
                lastmod_local = lastmod_dt.astimezone(start_dt.tzinfo)
                if not (start_dt <= lastmod_local <= end_dt):
                    continue

            key = url.rstrip("/").lower()
            raw_candidates[key] = candidate_from_url(
                source,
                url,
                "sitemap",
                date_value=entry.get("lastmod", ""),
                query=sitemap_url,
            )

    status = "checked"
    if raw_candidates:
        status += f"/sitemap found {len(raw_candidates)} raw candidates"
    elif checked:
        status += "/sitemap no recent content candidates"
    else:
        status += f"/sitemap unavailable ({robots_status})"
    if failures and not raw_candidates:
        status += f"; failures {len(failures)}"
    if rejected:
        status += f"; rejected {len(rejected)}"

    return list(raw_candidates.values()), status, rejected


def extract_date_near_anchor(anchor):
    text_parts = []
    for node in [anchor, anchor.parent, anchor.parent.parent if anchor.parent else None]:
        if node:
            text_parts.append(node.get_text(" ", strip=True))
    combined = " ".join(text_parts)
    parsed = content_extractor.parse_date(combined)
    return parsed


def scan_index_pages(source, run_date_str, coverage_hours=48, max_links_per_page=80):
    start_dt, end_dt = content_extractor.coverage_window(run_date_str, coverage_hours)
    base = source_base_url(source)
    index_paths = source.get("index_paths", [])
    raw_candidates = {}
    rejected = []
    checked = 0
    failures = []

    for index_path in index_paths:
        index_url = index_path if index_path.startswith("http") else urljoin(base, index_path)
        html, status = fetch_text(index_url, timeout=20)
        checked += 1
        if not html:
            failures.append(f"{index_url} ({status})")
            continue

        soup = BeautifulSoup(html, "html.parser")
        links_checked = 0
        for anchor in soup.find_all("a", href=True):
            if links_checked >= max_links_per_page:
                break
            href = anchor.get("href")
            url = urljoin(index_url, href).split("#")[0]
            if not is_same_domain(url, source["domain"]) or not looks_like_content_url(url):
                continue
            title = clean_title(anchor.get_text(" ", strip=True))
            if len(title) < 8:
                continue
            links_checked += 1
            date_dt = extract_date_near_anchor(anchor)
            rejection_reason = native_rejection_reason(
                url,
                title=title,
                allow_dated_listing=bool(date_dt),
            )
            if rejection_reason:
                rejected.append(rejection_item(source, url, "index_page", rejection_reason, title=title, query=index_url))
                continue
            if date_dt:
                date_local = date_dt.astimezone(start_dt.tzinfo)
                if not (start_dt <= date_local <= end_dt):
                    continue

            key = url.rstrip("/").lower()
            if key not in raw_candidates:
                summary = f"Found on index page: {index_url}"
                raw_candidates[key] = candidate_from_url(
                    source,
                    url,
                    "index_page",
                    title=title,
                    summary=summary,
                    date_value=date_dt.isoformat() if isinstance(date_dt, datetime) else "",
                    query=index_url,
                )

        time.sleep(config.SOURCE_DISCOVERY_DELAY_SECONDS)

    status = "checked"
    if raw_candidates:
        status += f"/index found {len(raw_candidates)} raw candidates"
    elif checked:
        status += "/index no content candidates"
    else:
        status += "/index not configured"
    if failures and not raw_candidates:
        status += f"; failures {len(failures)}"
    if rejected:
        status += f"; rejected {len(rejected)}"

    return list(raw_candidates.values()), status, rejected


def merge_candidate(existing, incoming):
    methods = set(existing.get("discovery_methods") or [])
    methods.update(incoming.get("discovery_methods") or [])
    existing["discovery_methods"] = sorted(methods)

    queries = set(existing.get("discovery_queries") or [])
    queries.update(incoming.get("discovery_queries") or [])
    existing["discovery_queries"] = sorted(queries)

    if len(incoming.get("summary", "")) > len(existing.get("summary", "")):
        existing["summary"] = incoming["summary"]
        existing["raw_summary"] = incoming.get("raw_summary", incoming["summary"])
    if incoming.get("title") and len(incoming["title"]) > len(existing.get("title", "")):
        existing["title"] = incoming["title"]
    if incoming.get("published_at") and not existing.get("published_at"):
        existing["published_at"] = incoming["published_at"]
        existing["date"] = incoming.get("date", existing.get("date", ""))
    return existing


def candidate_priority(item):
    methods = set(item.get("discovery_methods") or [])
    has_date = 1 if item.get("published_at") else 0
    sitemap = 1 if "sitemap" in methods else 0
    index = 1 if "index_page" in methods else 0
    timestamp = 0
    if item.get("published_at"):
        try:
            timestamp = item["published_at"].timestamp()
        except Exception:
            timestamp = 0
    return (has_date, sitemap, index, timestamp, len(item.get("summary", "")))


def cap_source_candidates(items):
    max_items = max(1, config.MAX_NATIVE_CANDIDATES_PER_SOURCE)
    ordered = sorted(items, key=candidate_priority, reverse=True)
    return ordered[:max_items], max(0, len(ordered) - max_items)


def discover_native_sources(run_date_str, coverage_hours=48, source_names=None):
    """
    Discovers candidates from configured official source sitemaps and index pages.
    If source_names is omitted, it scans sources that do not have RSS feeds.
    """
    requested_names = set(source_names or [])
    sources = [
        source
        for source in config.DISCOVERY_SOURCES
        if (
            source["name"] in requested_names
            if requested_names
            else source["name"] in config.SEARCH_ONLY_THINK_TANKS
        )
    ]
    candidates_by_key = {}
    status_notes = {}
    rejection_counts = {}
    rejected_samples = []

    print("[*] Starting source-native sitemap/index discovery for search-only think tanks...")
    for idx, source in enumerate(sources, 1):
        print(f"[*] Native source {idx}/{len(sources)}: {source['name']} ({source['domain']})")
        sitemap_items, sitemap_status, sitemap_rejected = scan_sitemaps(source, run_date_str, coverage_hours)
        index_items, index_status, index_rejected = scan_index_pages(source, run_date_str, coverage_hours)
        rejected_items = sitemap_rejected + index_rejected
        for rejected in rejected_items:
            reason = rejected.get("native_rejection_reason", "native_rejected_unknown")
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        rejected_samples.extend(rejected_items[:5])

        local_candidates = {}
        for item in sitemap_items + index_items:
            key = item["url"].rstrip("/").lower()
            if key in local_candidates:
                merge_candidate(local_candidates[key], item)
            else:
                local_candidates[key] = item

        capped_items, capped_count = cap_source_candidates(list(local_candidates.values()))
        found_count = 0
        for item in capped_items:
            key = item["url"].rstrip("/").lower()
            if key in candidates_by_key:
                merge_candidate(candidates_by_key[key], item)
            else:
                candidates_by_key[key] = item
                found_count += 1

        if found_count:
            cap_note = f"; capped {capped_count}" if capped_count else ""
            status_notes[source["name"]] = f"checked/native found {found_count} raw candidates ({sitemap_status}; {index_status}{cap_note})"
        else:
            status_notes[source["name"]] = f"checked/native no raw candidates ({sitemap_status}; {index_status})"

        time.sleep(config.SOURCE_DISCOVERY_DELAY_SECONDS)

    candidates = list(candidates_by_key.values())
    print(f"[+] Source-native discovery finished. Discovered {len(candidates)} unique raw candidates.")
    return candidates, status_notes, {
        "native_rejection_counts": dict(sorted(rejection_counts.items())),
        "native_rejected_samples": rejected_samples[:50],
        "native_rejected_total": sum(rejection_counts.values()),
    }


def discover_search_only_sources(run_date_str, coverage_hours=48):
    """
    Backward-compatible wrapper for sources without working RSS feeds.
    """
    return discover_native_sources(run_date_str, coverage_hours)
