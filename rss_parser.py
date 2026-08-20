import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import email.utils
import urllib3
import re
from config import RSS_FEEDS, SOURCE_DOMAINS, TIMEZONE_CANBERRA

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

def parse_feed_date(date_str):
    """
    Parses common RSS date formats (RFC 822, RFC 3339) and returns a timezone-aware datetime object.
    If parsing fails, returns None.
    """
    if not date_str:
        return None
    date_str = date_str.strip()
    
    # Try RFC 822 format (e.g., "Sun, 14 Jun 2026 03:00:00 GMT" or "Sun, 14 Jun 2026 13:00:00 +1000")
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        # Ensure it has timezone info; if not, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt
    except Exception:
        pass

    # Try ISO 8601 / RFC 3339 format (e.g., "2026-06-14T03:00:00Z" or "2026-06-14T03:00:00+10:00")
    try:
        # standard ISO format might have Z instead of +00:00
        clean_date = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt
    except Exception:
        pass

    # Fallback to regex-based attempts for custom strings
    try:
        # E.g., "2026-06-14 03:00:00"
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$", date_str)
        if match:
            dt = datetime(*map(int, match.groups()))
            return dt.replace(tzinfo=ZoneInfo("UTC"))
    except Exception:
        pass

    return None

def fetch_rss_items(run_date_str, coverage_hours=48):
    """
    Fetches articles from active RSS feeds published within the coverage window.
    Coverage window is: run_date at 3:00 AM Canberra time, minus coverage_hours.
    """
    canberra_tz = ZoneInfo(TIMEZONE_CANBERRA)
    
    # Calculate coverage window bounds in Canberra timezone
    run_date = datetime.strptime(run_date_str, "%Y-%m-%d")
    end_dt = run_date.replace(hour=3, minute=0, second=0, microsecond=0, tzinfo=canberra_tz)
    start_dt = end_dt - timedelta(hours=coverage_hours)
    
    print(f"[*] Coverage Window (Canberra Time): {start_dt.isoformat()} to {end_dt.isoformat()}")
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    discovered_items = []
    status_notes = {}
    
    for institution, url in RSS_FEEDS.items():
        print(f"[*] Scanning feed for: {institution}...")
        try:
            r = session.get(url, timeout=15, verify=False)
            if r.status_code != 200:
                print(f"    [-] Failed to fetch feed, status code: {r.status_code}")
                status_notes[institution] = f"checked/failed (HTTP {r.status_code})"
                continue
                
            # Parse feed with BeautifulSoup XML parser (or html.parser if XML fails)
            soup = BeautifulSoup(r.content, "xml")
            
            # Support both RSS (<item>) and Atom (<entry>) tags
            items = soup.find_all("item")
            is_atom = False
            if not items:
                items = soup.find_all("entry")
                is_atom = True
                
            if not items:
                # Retry parsing with html.parser if xml parser missed it (e.g. malformed tags)
                soup = BeautifulSoup(r.content, "html.parser")
                items = soup.find_all("item") or soup.find_all("entry")
                
            print(f"    [+] Found {len(items)} raw feed entries.")
            
            count_found = 0
            for entry in items:
                # Extract URL/Link
                link_tag = entry.find("link")
                url_str = ""
                if link_tag:
                    if is_atom and link_tag.get("href"):
                        url_str = link_tag.get("href").strip()
                    else:
                        url_str = link_tag.text.strip()
                
                # If no link, skip
                if not url_str:
                    continue
                
                # Clean URL (remove query parameters like utm_source)
                url_str = url_str.split("?")[0].split("#")[0]
                
                # Extract title
                title = entry.find("title")
                title_str = title.text.strip() if title else "Untitled"
                
                # Extract publication date
                pub_date_tag = entry.find("pubDate") or entry.find("published") or entry.find("updated") or entry.find("dc:date")
                pub_date_str = pub_date_tag.text.strip() if pub_date_tag else ""
                
                pub_dt = parse_feed_date(pub_date_str)
                
                # If date could not be parsed, skip or default (we must be strict about 48-hour coverage)
                if not pub_dt:
                    continue
                
                # Convert pub date to Canberra time for comparisons
                pub_dt_canberra = pub_dt.astimezone(canberra_tz)
                
                # Check if item falls within coverage window
                if start_dt <= pub_dt_canberra <= end_dt:
                    # Extract other fields
                    author_tag = entry.find("dc:creator") or entry.find("author") or entry.find("creator")
                    author_str = author_tag.text.strip() if author_tag else ""
                    # strip email from authors like "email@address.com (Name)"
                    if author_str and "(" in author_str and author_str.endswith(")"):
                        match = re.search(r"\(([^)]+)\)", author_str)
                        if match:
                            author_str = match.group(1)
                    
                    desc_tag = entry.find("description") or entry.find("summary") or entry.find("content:encoded")
                    desc_str = desc_tag.text.strip() if desc_tag else ""
                    # clean HTML tags from description if any
                    desc_str = BeautifulSoup(desc_str, "html.parser").get_text(separator=" ").strip()
                    # Truncate summary if too long
                    if len(desc_str) > 1000:
                        desc_str = desc_str[:1000] + "..."
                        
                    categories = [cat.text.strip() for cat in entry.find_all("category") if cat.text]
                    
                    item_data = {
                        "title": title_str,
                        "institution": institution,
                        "date": pub_dt_canberra.strftime("%d %B %Y"), # Australian date format
                        "author": author_str,
                        "tags": categories,
                        "summary": desc_str, # Will be rewritten/analyzed by LLM
                        "raw_summary": desc_str,
                        "url": url_str,
                        "published_at": pub_dt_canberra,
                        "source_domain": SOURCE_DOMAINS.get(institution, ""),
                        "discovery_method": "rss",
                        "discovery_methods": ["rss"],
                        "discovery_query": "",
                        "discovery_queries": [],
                        "candidate_reason": "Published in RSS feed within the configured coverage window.",
                    }
                    discovered_items.append(item_data)
                    count_found += 1
            
            print(f"    [+] {count_found} items matching the 48-hour coverage window.")
            if count_found > 0:
                status_notes[institution] = f"checked/found {count_found} items"
            else:
                status_notes[institution] = "checked/no qualifying items"
                
        except Exception as e:
            print(f"    [-] Error scanning feed: {e}")
            status_notes[institution] = f"checked/failed ({str(e)[:50]})"
            
    return discovered_items, status_notes

if __name__ == "__main__":
    # Test execution
    import sys
    today_str = datetime.now(ZoneInfo(TIMEZONE_CANBERRA)).strftime("%Y-%m-%d")
    items, notes = fetch_rss_items(today_str)
    print(f"\nDiscovered {len(items)} total items.")
    for i, item in enumerate(items[:3]):
        print(f"\nItem {i+1}:")
        print(f"Title: {item['title']}")
        print(f"Institution: {item['institution']}")
        print(f"Date: {item['date']}")
        print(f"URL: {item['url']}")
