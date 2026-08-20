import re
import time
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

import config

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def clean_result_url(href):
    """
    Converts DuckDuckGo redirect URLs into canonical result URLs.
    """
    url_str = href or ""
    if "/l/?uddg=" in url_str:
        parsed = urlparse(url_str)
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            url_str = qs["uddg"][0]
    return url_str.split("#")[0].strip()


def search_ddg_html(query, max_results=None):
    """
    Queries DuckDuckGo HTML search and parses the results.
    """
    max_results = max_results or config.MAX_DDG_RESULTS_PER_QUERY
    url = "https://html.duckduckgo.com/html/"
    data = {"q": query}
    results = []

    try:
        response = requests.post(url, headers=HEADERS, data=data, timeout=20, verify=False)
        if response.status_code != 200:
            print(f"    [-] DDG HTML query failed with status code {response.status_code}")
            return []

        soup = BeautifulSoup(response.content, "html.parser")
        result_divs = soup.find_all("div", class_="result")

        for div in result_divs:
            title_tag = div.find("a", class_="result__a")
            snippet_tag = div.find("a", class_="result__snippet")
            if not title_tag:
                continue

            title = title_tag.text.strip()
            url_str = clean_result_url(title_tag.get("href", ""))
            snippet = snippet_tag.text.strip() if snippet_tag else ""
            if not title or not url_str:
                continue

            results.append(
                {
                    "title": re.sub(r"\s+", " ", title),
                    "url": url_str,
                    "snippet": re.sub(r"\s+", " ", snippet),
                }
            )
            if len(results) >= max_results:
                break
        return results
    except Exception as exc:
        print(f"    [-] DDG HTML scraping exception: {exc}")
        return []


def build_source_query(domain, terms):
    quoted_terms = []
    for term in terms:
        if " " in term:
            quoted_terms.append(f'"{term}"')
        else:
            quoted_terms.append(term)
    return f"site:{domain} (" + " OR ".join(quoted_terms) + ")"


def merge_candidate(existing, candidate):
    existing_methods = set(existing.get("discovery_methods", []))
    existing_methods.update(candidate.get("discovery_methods", []))
    existing["discovery_methods"] = sorted(existing_methods)

    existing_queries = set(existing.get("discovery_queries", []))
    existing_queries.update(candidate.get("discovery_queries", []))
    existing["discovery_queries"] = sorted(existing_queries)

    if len(candidate.get("summary", "")) > len(existing.get("summary", "")):
        existing["summary"] = candidate["summary"]
    return existing


def search_think_tanks_via_ddg(run_date_str, coverage_hours=48, source_names=None):
    """
    Runs source-specific recall-first DuckDuckGo searches for search-only think tanks.
    Page extraction later verifies dates and content relevance.
    """
    print("[*] Starting source-specific DuckDuckGo search scan for search-only think tanks...")

    allowed_sources = set(source_names or config.SEARCH_ONLY_THINK_TANKS)
    search_sources = [
        source
        for source in config.DISCOVERY_SOURCES
        if source["name"] in allowed_sources
    ]

    candidates_by_key = {}
    status_notes = {
        source["name"]: "checked/no raw candidates"
        for source in search_sources
    }

    for source_idx, source in enumerate(search_sources, 1):
        institution = source["name"]
        domain = source["domain"]
        source_count = 0
        print(f"[*] DDG source {source_idx}/{len(search_sources)}: {institution} ({domain})")

        for group_idx, terms in enumerate(config.SEARCH_QUERY_GROUPS, 1):
            query = build_source_query(domain, terms)
            print(f"    [*] Query group {group_idx}/{len(config.SEARCH_QUERY_GROUPS)}")
            raw_results = search_ddg_html(query)
            print(f"    [+] DDG returned {len(raw_results)} results.")

            for item in raw_results:
                url_lower = item["url"].lower()
                if domain.lower() not in url_lower:
                    continue

                key = item["url"].rstrip("/").lower()
                candidate = {
                    "title": item["title"],
                    "institution": institution,
                    "date": "",
                    "author": "",
                    "summary": item["snippet"],
                    "url": item["url"],
                    "item_type": "report",
                    "source_domain": domain,
                    "discovery_method": "ddg_html",
                    "discovery_methods": ["ddg_html"],
                    "discovery_query": query,
                    "discovery_queries": [query],
                    "candidate_reason": "Source-specific DDG topic query matched the institution domain.",
                    "run_date": run_date_str,
                    "coverage_hours": coverage_hours,
                }

                if key in candidates_by_key:
                    merge_candidate(candidates_by_key[key], candidate)
                else:
                    candidates_by_key[key] = candidate
                    source_count += 1

            if group_idx < len(config.SEARCH_QUERY_GROUPS):
                time.sleep(config.DDG_SEARCH_DELAY_SECONDS)

        status_notes[institution] = (
            f"checked/found {source_count} raw candidates"
            if source_count
            else "checked/no raw candidates"
        )

    discovered_items = list(candidates_by_key.values())
    print(f"[+] DuckDuckGo search scan finished. Discovered {len(discovered_items)} unique raw candidates.")
    return discovered_items, status_notes


if __name__ == "__main__":
    items, notes = search_think_tanks_via_ddg("2026-06-22")
    print(f"Found {len(items)} items.")
    for idx, item in enumerate(items[:3]):
        print(f"{idx + 1}. {item['institution']}: {item['title']} - {item['url']}")
