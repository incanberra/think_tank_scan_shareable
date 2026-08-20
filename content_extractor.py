import email.utils
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import urllib3
from bs4 import BeautifulSoup

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

DATE_META_NAMES = [
    "article:published_time",
    "article:modified_time",
    "og:updated_time",
    "date",
    "pubdate",
    "publishdate",
    "publish_date",
    "publication_date",
    "dc.date",
    "dc.date.issued",
    "dcterms.created",
    "dcterms.date",
    "sailthru.date",
]

AUTHOR_META_NAMES = [
    "author",
    "article:author",
    "dc.creator",
    "dcterms.creator",
    "sailthru.author",
]

TITLE_META_NAMES = ["og:title", "twitter:title"]
DESCRIPTION_META_NAMES = ["description", "og:description", "twitter:description"]
STRUCTURED_BODY_FIELDS = ["articleBody", "description", "text"]


def utc_now_iso():
    return datetime.now(ZoneInfo("UTC")).isoformat()


def normalize_cache_url(url):
    parsed = urlparse(str(url or "").strip())
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or parsed.path,
            "",
            parsed.query,
            "",
        )
    )


def cache_key_for_url(url):
    normalized = normalize_cache_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def cache_path_for_url(url):
    return os.path.join(config.ENRICHMENT_CACHE_DIR, f"{cache_key_for_url(url)}.json")


def parse_cached_datetime(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def serialize_page_data(page_data):
    payload = dict(page_data)
    for field in ["extracted_date_dt", "weak_date_dt"]:
        dt_value = payload.get(field)
        if isinstance(dt_value, datetime):
            payload[field] = dt_value.isoformat()
    return payload


def deserialize_page_data(page_data):
    payload = dict(page_data or {})
    for field in ["extracted_date_dt", "weak_date_dt"]:
        dt_value = payload.get(field)
        if isinstance(dt_value, str):
            payload[field] = parse_date(dt_value)
    return payload


def load_cache_entry(url):
    if not config.ENABLE_ENRICHMENT_CACHE or not url:
        return None
    path = cache_path_for_url(url)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def cache_entry_is_fresh(entry):
    saved_at = parse_cached_datetime(entry.get("cache_saved_at") if entry else "")
    if not saved_at:
        return False
    age_hours = (datetime.now(ZoneInfo("UTC")) - saved_at.astimezone(ZoneInfo("UTC"))).total_seconds() / 3600
    return age_hours <= config.ENRICHMENT_CACHE_MAX_AGE_HOURS


def page_data_from_cache(entry, cache_status):
    page_data = deserialize_page_data(entry.get("page_data", {}))
    page_data["cache_status"] = cache_status
    page_data["cache_saved_at"] = entry.get("cache_saved_at", "")
    if entry.get("etag"):
        page_data["cache_etag"] = entry["etag"]
    if entry.get("last_modified"):
        page_data["cache_last_modified"] = entry["last_modified"]
    return page_data


def write_cache_entry(request_url, page_data, response_headers=None):
    if not config.ENABLE_ENRICHMENT_CACHE or not request_url:
        return

    response_headers = response_headers or {}
    page_data = dict(page_data)
    if page_data.get("canonical_url"):
        page_data["canonical_url"] = resolve_canonical_url(page_data["canonical_url"], request_url)
    entry = {
        "request_url": request_url,
        "canonical_url": page_data.get("canonical_url") or request_url,
        "cache_saved_at": utc_now_iso(),
        "etag": response_headers.get("ETag") or response_headers.get("etag") or page_data.get("cache_etag") or "",
        "last_modified": (
            response_headers.get("Last-Modified")
            or response_headers.get("last-modified")
            or page_data.get("cache_last_modified")
            or ""
        ),
        "content_type": response_headers.get("Content-Type") or response_headers.get("content-type") or "",
        "page_data": serialize_page_data(page_data),
    }

    os.makedirs(config.ENRICHMENT_CACHE_DIR, exist_ok=True)
    cache_urls = {request_url, entry["canonical_url"]}
    for cache_url in cache_urls:
        path = cache_path_for_url(cache_url)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(entry, handle, indent=2, ensure_ascii=False)


def repair_enrichment_cache(cache_dir=None):
    cache_dir = cache_dir or config.ENRICHMENT_CACHE_DIR
    summary = {"files_seen": 0, "files_repaired": 0, "files_removed": 0}
    if not os.path.isdir(cache_dir):
        return summary

    for name in os.listdir(cache_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(cache_dir, name)
        summary["files_seen"] += 1
        try:
            with open(path, "r", encoding="utf-8") as handle:
                entry = json.load(handle)
        except Exception:
            continue

        request_url = entry.get("request_url") or entry.get("canonical_url") or ""
        page_data = entry.get("page_data") or {}
        canonical_url = page_data.get("canonical_url") or entry.get("canonical_url") or request_url
        repaired = resolve_canonical_url(canonical_url, request_url)
        if not repaired:
            try:
                os.remove(path)
                summary["files_removed"] += 1
            except OSError:
                pass
            continue
        if repaired != canonical_url or entry.get("canonical_url") != repaired:
            entry["canonical_url"] = repaired
            page_data["canonical_url"] = repaired
            entry["page_data"] = page_data
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(entry, handle, indent=2, ensure_ascii=False)
            summary["files_repaired"] += 1

    return summary


def coverage_window(run_date_str, coverage_hours=None):
    """
    Returns the Canberra-time coverage window for a run date.
    """
    coverage_hours = coverage_hours or config.COVERAGE_WINDOW_HOURS
    canberra_tz = ZoneInfo(config.TIMEZONE_CANBERRA)
    run_date = datetime.strptime(run_date_str, "%Y-%m-%d")
    end_dt = run_date.replace(hour=3, minute=0, second=0, microsecond=0, tzinfo=canberra_tz)
    return end_dt - timedelta(hours=coverage_hours), end_dt


def parse_date(value):
    """
    Parses common page metadata dates into timezone-aware datetimes.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(config.TIMEZONE_CANBERRA))
        return dt

    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None

    try:
        dt = email.utils.parsedate_to_datetime(text)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(config.TIMEZONE_CANBERRA))
            return dt
    except Exception:
        pass

    iso_text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(config.TIMEZONE_CANBERRA))
        return dt
    except Exception:
        pass

    formats = [
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=ZoneInfo(config.TIMEZONE_CANBERRA))
        except Exception:
            continue

    match = re.search(
        r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})",
        text,
        re.IGNORECASE,
    )
    if match:
        return parse_date(match.group(1))

    match = re.search(
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4})",
        text,
        re.IGNORECASE,
    )
    if match:
        return parse_date(match.group(1))

    return None


def format_display_date(dt):
    if not dt:
        return ""
    canberra_tz = ZoneInfo(config.TIMEZONE_CANBERRA)
    return dt.astimezone(canberra_tz).strftime("%d %B %Y")


def get_meta_content(soup, names):
    for name in names:
        selectors = [
            {"name": name},
            {"property": name},
            {"itemprop": name},
        ]
        for attrs in selectors:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                return tag["content"].strip()
    return ""


def iter_json_ld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                yield item
        elif isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    yield item
            yield data


def clean_text(value):
    if not value:
        return ""
    if "<" not in str(value):
        return re.sub(r"\s+", " ", str(value)).strip()
    text = BeautifulSoup(str(value), "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def resolve_canonical_url(raw_url, fallback_url):
    """
    Resolves and validates canonical/OG URLs before they influence caches,
    ledgers, or deduplication. Some sites emit malformed absolute strings such
    as "https://hosthttps://host"; those must fall back to the request URL.
    """
    fallback = str(fallback_url or "").split("#")[0].strip()
    raw = str(raw_url or "").split("#")[0].strip()
    candidate = raw or fallback
    if not candidate:
        return ""

    resolved = urljoin(fallback, candidate)
    parsed = urlparse(resolved)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return fallback
    netloc = parsed.netloc.lower()
    remainder = f"{parsed.netloc}{parsed.path}".lower()
    if "http://" in remainder or "https://" in remainder or netloc.endswith("http:") or netloc.endswith("https:"):
        return fallback
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", parsed.query, ""))


def content_hash_for_text(value):
    normalized = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def safe_console_text(value):
    return str(value or "").encode("ascii", errors="replace").decode("ascii")


def extract_json_ld_metadata(soup):
    title = ""
    author = ""
    date_value = ""
    for item in iter_json_ld(soup):
        if not isinstance(item, dict):
            continue
        item_type = item.get("@type", "")
        if isinstance(item_type, list):
            item_type = " ".join(item_type)
        type_text = str(item_type).lower()
        if not any(token in type_text for token in ["article", "newsarticle", "blogposting", "report", "event"]):
            continue
        title = title or clean_text(item.get("headline") or item.get("name"))
        date_value = date_value or item.get("datePublished") or item.get("dateCreated") or item.get("dateModified")
        if not author:
            author_data = item.get("author") or item.get("creator")
            if isinstance(author_data, list):
                author_names = []
                for author_item in author_data:
                    if isinstance(author_item, dict):
                        author_names.append(clean_text(author_item.get("name")))
                    else:
                        author_names.append(clean_text(author_item))
                author = ", ".join([name for name in author_names if name])
            elif isinstance(author_data, dict):
                author = clean_text(author_data.get("name"))
            else:
                author = clean_text(author_data)
    return {"title": title, "author": author, "date": date_value}


def extract_json_ld_body(soup):
    bodies = []
    for item in iter_json_ld(soup):
        if not isinstance(item, dict):
            continue
        item_type = item.get("@type", "")
        if isinstance(item_type, list):
            item_type = " ".join(item_type)
        type_text = str(item_type).lower()
        if type_text and not any(token in type_text for token in ["article", "newsarticle", "blogposting", "report"]):
            continue
        for field in STRUCTURED_BODY_FIELDS:
            text = clean_text(item.get(field))
            if len(text) >= 80:
                bodies.append(text)
                break
    return "\n\n".join(dict.fromkeys(bodies))


def find_canonical_url(soup, fallback_url):
    tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    if tag and tag.get("href"):
        return resolve_canonical_url(tag["href"], fallback_url)
    og_url = get_meta_content(soup, ["og:url"])
    if og_url:
        return resolve_canonical_url(og_url, fallback_url)
    return resolve_canonical_url(fallback_url, fallback_url)


def remove_boilerplate(soup):
    for tag in soup(["script", "style", "noscript", "svg", "form", "iframe"]):
        tag.decompose()
    boilerplate_terms = [
        "nav",
        "menu",
        "footer",
        "header",
        "subscribe",
        "newsletter",
        "cookie",
        "share",
        "social",
        "advert",
        "related",
        "promo",
        "breadcrumb",
    ]
    for tag in list(soup.find_all(True)):
        if getattr(tag, "attrs", None) is None:
            continue
        attrs = " ".join(
            [
                " ".join(tag.get("class", [])) if isinstance(tag.get("class"), list) else str(tag.get("class", "")),
                str(tag.get("id", "")),
                str(tag.get("role", "")),
                str(tag.get("aria-label", "")),
            ]
        ).lower()
        if any(term in attrs for term in boilerplate_terms):
            tag.decompose()


def extract_candidate_roots(soup):
    selectors = [
        "article",
        "main",
        "[itemprop='articleBody']",
        "[class*='article-body']",
        "[class*='article__body']",
        "[class*='story-body']",
        "[class*='post-content']",
        "[class*='entry-content']",
        "[class*='field--name-body']",
        "[class*='content-body']",
        "[class*='rich-text']",
        "[data-testid*='article']",
    ]
    roots = []
    for selector in selectors:
        for root in soup.select(selector):
            if root not in roots:
                roots.append(root)
    if not roots:
        roots.append(soup.body or soup)
    return roots


def extract_text_from_html(html, fallback_description=""):
    soup = BeautifulSoup(html, "html.parser")
    structured_body = extract_json_ld_body(soup)
    remove_boilerplate(soup)
    text_blocks = []
    for root in extract_candidate_roots(soup):
        for tag in root.find_all(["h1", "h2", "h3", "p", "li", "blockquote"], recursive=True):
            text = clean_text(tag.get_text(" "))
            if len(text) < 30:
                continue
            if text.lower() in {item.lower() for item in text_blocks[-5:]}:
                continue
            text_blocks.append(text)
    if not text_blocks:
        body_text = clean_text((soup.body or soup).get_text(" "))
        if body_text:
            text_blocks.append(body_text)
    if structured_body and structured_body not in text_blocks:
        text_blocks.insert(0, structured_body)
    if not text_blocks and fallback_description:
        text_blocks.append(clean_text(fallback_description))
    text = "\n\n".join(text_blocks)
    return text[: config.FULL_TEXT_CHAR_LIMIT]


def classify_content_type(url, title, text):
    haystack = f"{url} {title} {text[:1000]}".lower()
    if any(token in haystack for token in ["/event", "webinar", "conference", "panel discussion"]):
        return "event"
    if any(token in haystack for token in ["podcast", "video", "youtube", "listen", "watch"]):
        return "podcast"
    return "report"


def assess_date_status(item, extracted_dt, run_date_str, coverage_hours, weak_dt=None):
    start_dt, end_dt = coverage_window(run_date_str, coverage_hours)
    item_published_dt = parse_date(item.get("published_at"))
    item_date_dt = parse_date(item.get("date"))
    candidate_dt = extracted_dt or item_published_dt or item_date_dt or weak_dt
    if not candidate_dt:
        return {
            "date_status": "date_unknown",
            "date_confidence": "low",
            "published_at_verified": "",
            "published_at_display": item.get("date", ""),
        }
    canberra_dt = candidate_dt.astimezone(ZoneInfo(config.TIMEZONE_CANBERRA))
    in_window = start_dt <= canberra_dt <= end_dt
    if extracted_dt or item_published_dt:
        confidence = "high"
    elif item_date_dt:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "date_status": "verified_in_window" if in_window else "verified_out_of_window",
        "date_confidence": confidence,
        "published_at_verified": canberra_dt.isoformat(),
        "published_at_display": format_display_date(canberra_dt),
    }


def is_pdf_response(response, url, content_type):
    url_path = urlparse(response.url or url).path.lower()
    return (
        "pdf" in (content_type or "").lower()
        or url_path.endswith(".pdf")
        or response.content[:4] == b"%PDF"
    )


def parse_pdf_metadata_date(value):
    if not value:
        return None
    text = str(value).strip()
    if text.startswith("D:"):
        match = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", text)
        if match:
            parts = match.groups()
            year = int(parts[0])
            month = int(parts[1] or 1)
            day = int(parts[2] or 1)
            hour = int(parts[3] or 0)
            minute = int(parts[4] or 0)
            second = int(parts[5] or 0)
            return datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo(config.TIMEZONE_CANBERRA))
    return parse_date(text)


def clean_pdf_text(text):
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_from_response(response, url):
    if PdfReader is None:
        return {
            "extraction_status": "skipped_pdf_missing_dependency",
            "extraction_error": "pypdf is not installed",
            "http_status": response.status_code,
            "canonical_url": response.url or url,
            "extracted_text": "",
            "extracted_text_chars": 0,
            "content_type_guess": "report",
            "source_host": urlparse(response.url or url).netloc,
        }

    try:
        reader = PdfReader(BytesIO(response.content))
        metadata = reader.metadata or {}
        title = clean_text(getattr(metadata, "title", "") or metadata.get("/Title", ""))
        author = clean_text(getattr(metadata, "author", "") or metadata.get("/Author", ""))
        raw_date = (
            getattr(metadata, "modification_date", None)
            or getattr(metadata, "creation_date", None)
            or metadata.get("/ModDate", "")
            or metadata.get("/CreationDate", "")
        )
        parsed_date = parse_pdf_metadata_date(raw_date)
        page_texts = []
        for page in reader.pages[: max(1, config.PDF_TEXT_MAX_PAGES)]:
            page_text = page.extract_text() or ""
            if page_text.strip():
                page_texts.append(clean_pdf_text(page_text))
        text = clean_pdf_text("\n\n".join(page_texts))[: config.FULL_TEXT_CHAR_LIMIT]
        canonical_url = (response.url or url).split("#")[0].strip()
        return {
            "extraction_status": "ok" if text else "empty_text",
            "http_status": response.status_code,
            "canonical_url": canonical_url,
            "extracted_title": title,
            "extracted_author": author,
            "extracted_date_raw": str(raw_date or ""),
            "extracted_date_dt": parsed_date,
            "extracted_date": format_display_date(parsed_date),
            "extracted_text": text,
            "extracted_text_chars": len(text),
            "content_type_guess": "report",
            "source_host": urlparse(canonical_url or url).netloc,
            "content_format": "pdf",
            "pdf_pages_checked": min(len(reader.pages), max(1, config.PDF_TEXT_MAX_PAGES)),
        }
    except Exception as exc:
        return {
            "extraction_status": "pdf_extract_failed",
            "extraction_error": str(exc)[:200],
            "http_status": response.status_code,
            "canonical_url": response.url or url,
            "extracted_text": "",
            "extracted_text_chars": 0,
            "content_type_guess": "report",
            "source_host": urlparse(response.url or url).netloc,
            "content_format": "pdf",
        }


def extract_page(url, timeout=20):
    """
    Fetches a page and extracts metadata plus readable text.
    """
    cached_entry = load_cache_entry(url)
    if cached_entry and cache_entry_is_fresh(cached_entry):
        return page_data_from_cache(cached_entry, "hit_fresh")

    headers = dict(HEADERS)
    if cached_entry:
        if cached_entry.get("etag"):
            headers["If-None-Match"] = cached_entry["etag"]
        if cached_entry.get("last_modified"):
            headers["If-Modified-Since"] = cached_entry["last_modified"]

    try:
        response = requests.get(url, headers=headers, timeout=timeout, verify=False)
    except Exception as exc:
        if cached_entry:
            return page_data_from_cache(cached_entry, "stale_fallback_fetch_failed")
        return {
            "extraction_status": "fetch_failed",
            "extraction_error": str(exc)[:200],
            "http_status": None,
            "cache_status": "miss",
        }

    content_type = response.headers.get("content-type", "")
    if response.status_code == 304 and cached_entry:
        page_data = page_data_from_cache(cached_entry, "revalidated_304")
        write_cache_entry(url, page_data, response.headers)
        return page_data

    if response.status_code != 200:
        if cached_entry:
            return page_data_from_cache(cached_entry, f"stale_fallback_http_{response.status_code}")
        return {
            "extraction_status": "http_failed",
            "extraction_error": f"HTTP {response.status_code}",
            "http_status": response.status_code,
            "cache_status": "miss",
        }

    if is_pdf_response(response, url, content_type):
        result = extract_pdf_from_response(response, url)
        result["cache_status"] = "refreshed" if cached_entry else "miss"
        write_cache_entry(url, result, response.headers)
        return result

    if "html" not in content_type.lower() and "<html" not in response.text[:500].lower():
        result = {
            "extraction_status": "skipped_non_html",
            "extraction_error": content_type[:120],
            "http_status": response.status_code,
            "canonical_url": response.url or url,
            "extracted_text": "",
            "extracted_text_chars": 0,
            "cache_status": "refreshed" if cached_entry else "miss",
        }
        write_cache_entry(url, result, response.headers)
        return result

    soup = BeautifulSoup(response.text, "html.parser")
    json_ld = extract_json_ld_metadata(soup)
    title = (
        get_meta_content(soup, TITLE_META_NAMES)
        or json_ld.get("title")
        or clean_text(soup.title.get_text(" ") if soup.title else "")
    )
    author = get_meta_content(soup, AUTHOR_META_NAMES) or json_ld.get("author")
    raw_date = get_meta_content(soup, DATE_META_NAMES) or json_ld.get("date")
    parsed_date = parse_date(raw_date)
    fallback_description = get_meta_content(soup, DESCRIPTION_META_NAMES)
    text = extract_text_from_html(response.text, fallback_description=fallback_description)
    canonical_url = find_canonical_url(soup, response.url or url)
    weak_date_raw = response.headers.get("Last-Modified") or response.headers.get("last-modified") or ""
    weak_date_dt = parse_date(weak_date_raw)

    result = {
        "extraction_status": "ok" if text else "empty_text",
        "http_status": response.status_code,
        "canonical_url": canonical_url,
        "extracted_title": title,
        "extracted_author": author,
        "extracted_date_raw": raw_date or "",
        "extracted_date_dt": parsed_date,
        "extracted_date": format_display_date(parsed_date),
        "weak_date_raw": weak_date_raw,
        "weak_date_dt": weak_date_dt if not parsed_date else None,
        "weak_date": format_display_date(weak_date_dt) if weak_date_dt and not parsed_date else "",
        "extracted_text": text,
        "extracted_text_chars": len(text),
        "content_type_guess": classify_content_type(canonical_url, title, text),
        "source_host": urlparse(canonical_url or url).netloc,
        "content_format": "html",
        "cache_status": "refreshed" if cached_entry else "miss",
    }
    write_cache_entry(url, result, response.headers)
    return result


def enrich_item(item, run_date_str, coverage_hours=None, fetch_pages=True):
    """
    Adds page metadata, full text, date verification, and provenance fields.
    """
    coverage_hours = coverage_hours or config.COVERAGE_WINDOW_HOURS
    enriched = dict(item)
    enriched.setdefault("raw_summary", item.get("summary", ""))
    enriched.setdefault("discovery_methods", [item.get("discovery_method", "rss")])
    enriched.setdefault("discovery_queries", [])

    if not fetch_pages or not item.get("url"):
        page_data = {
            "extraction_status": "skipped",
            "extracted_text": "",
            "extracted_text_chars": 0,
            "canonical_url": item.get("url", ""),
            "extracted_date_dt": None,
        }
    else:
        page_data = extract_page(item["url"])

    if page_data.get("canonical_url") and item.get("url"):
        page_data["canonical_url"] = resolve_canonical_url(page_data["canonical_url"], item["url"])
    extracted_dt = page_data.pop("extracted_date_dt", None)
    weak_dt = page_data.pop("weak_date_dt", None)
    date_assessment = assess_date_status(enriched, extracted_dt, run_date_str, coverage_hours, weak_dt=weak_dt)
    enriched.update(page_data)
    enriched.update(date_assessment)

    if page_data.get("canonical_url"):
        enriched["canonical_url"] = page_data["canonical_url"]
    if page_data.get("extracted_title") and len(page_data["extracted_title"]) > len(enriched.get("title", "")):
        enriched["title"] = page_data["extracted_title"]
    if page_data.get("extracted_author") and not enriched.get("author"):
        enriched["author"] = page_data["extracted_author"]
    if date_assessment.get("published_at_display") and date_assessment["date_status"] != "date_unknown":
        enriched["date"] = date_assessment["published_at_display"]
    if page_data.get("content_type_guess") and not enriched.get("item_type"):
        enriched["item_type"] = page_data["content_type_guess"]
    enriched["content_hash"] = content_hash_for_text(enriched.get("extracted_text", ""))

    return enriched


def enrich_items(items, run_date_str, coverage_hours=None, fetch_pages=True):
    """
    Enriches all candidates and returns (items, audit).
    """
    enriched_items = []
    audit = {
        "total_candidates": len(items),
        "fetch_pages": fetch_pages,
        "extraction_status_counts": {},
        "date_status_counts": {},
        "cache_status_counts": {},
    }
    for idx, item in enumerate(items, 1):
        institution = safe_console_text(item.get("institution"))
        title = safe_console_text(item.get("title"))[:80]
        print(f"[*] Enriching candidate {idx}/{len(items)}: {institution} - {title}")
        enriched = enrich_item(item, run_date_str, coverage_hours, fetch_pages=fetch_pages)
        enriched_items.append(enriched)
        extraction_status = enriched.get("extraction_status", "unknown")
        date_status = enriched.get("date_status", "unknown")
        cache_status = enriched.get("cache_status", "none")
        audit["extraction_status_counts"][extraction_status] = audit["extraction_status_counts"].get(extraction_status, 0) + 1
        audit["date_status_counts"][date_status] = audit["date_status_counts"].get(date_status, 0) + 1
        audit["cache_status_counts"][cache_status] = audit["cache_status_counts"].get(cache_status, 0) + 1
    return enriched_items, audit
