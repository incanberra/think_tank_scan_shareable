"""Publisher-specific dates near the article heading, never listing-wide dates."""
import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import config


def extract(soup, url, parse_date):
    host = (urlparse(url).hostname or "").removeprefix("www.")
    if host == "csis.org":
        tag = soup.select_one('meta[name="citation_publication_date"]')
        if tag:
            value = tag.get("content", "")
            match = re.fullmatch(r"[A-Za-z]{3}, (\d{2}/\d{2}/\d{4}) - (\d{2}:\d{2})", value)
            if match:
                date = datetime.strptime(" ".join(match.groups()), "%m/%d/%Y %H:%M").replace(tzinfo=ZoneInfo("America/New_York"))
                return date, "csis_citation_publication_date", value
            date = parse_date(value)
            if date:
                return date, "csis_citation_publication_date", value
    if host == "rusi.org":
        tag = soup.select_one('[aria-label="published date"]')
        if tag:
            value = tag.get_text(" ", strip=True)
            date = parse_date(value)
            if date:
                return date, "rusi_visible_publication_date", value
    # Stay in the heading's immediate article/header region. Multiple distinct
    # dates indicate a listing or ambiguity and must not establish publication.
    heading = soup.find("h1")
    if not heading:
        return None, "", ""
    parent = heading.parent
    for _ in range(3):
        if not parent or parent.name in {"body", "html", "[document]"}:
            break
        selectors = 'time[datetime], [itemprop="datePublished"]'
        if host == "cnas.org":
            selectors += ', p.sans-serif.uppercase'
        values = []
        for tag in parent.select(selectors):
            if "modif" in str(tag.attrs).lower():
                continue
            value = tag.get("datetime") or tag.get("content") or tag.get_text(" ", strip=True)
            date = parse_date(value)
            if date and len(value) < 100:
                values.append((date, value))
        dates = {date.isoformat() for date, _ in values}
        if len(dates) == 1:
            return values[0][0], f"{host}_visible_publication_date", values[0][1]
        if len(dates) > 1:
            return None, "", ""
        parent = parent.parent
    return None, "", ""
