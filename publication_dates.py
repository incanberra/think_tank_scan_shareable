"""Publisher-specific dates near the article heading, never listing-wide dates."""
import re
import json
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import config

MONTH_DATE = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}"

def unique_date(tags, parse_date):
    values = []
    for tag in tags:
        if any(word in str(tag.attrs).lower() for word in ('modified', 'updated', 'start-datetime')):
            continue
        value = tag.get('datetime') or tag.get('content') or tag.get_text(' ', strip=True)
        if len(value) <= 100:
            date = parse_date(re.sub(r'^(?:Published|Publication date)\s*:?\s*', '', value, flags=re.I))
            if date:
                values.append((date, value))
    if len({date.isoformat() for date, _ in values}) == 1:
        return values[0]
    return None, ''

def event_dates(soup, url, parse_date):
    """Hudson's current event hero; never use related events as publication dates."""
    if (urlparse(url).hostname or '').removeprefix('www.') != 'hudson.org':
        return None, None, ''
    tag = soup.select_one('.hero__event-date .block-field-block-node-event-detail-field-start-datetime')
    if not tag:
        return None, None, ''
    value = tag.get_text(' ', strip=True)
    match = re.fullmatch(r'([A-Za-z]+)\s+(\d{1,2})\s*[-–]\s*(\d{1,2}),?\s+(\d{4})', value)
    if match:
        month, start, end, year = match.groups()
        return parse_date(f'{month} {start}, {year}'), parse_date(f'{month} {end}, {year}'), value
    if re.fullmatch(MONTH_DATE, value, flags=re.I):
        return parse_date(value), None, value
    return None, None, ''


def extract(soup, url, parse_date):
    host = (urlparse(url).hostname or "").removeprefix("www.")
    if host == 'carnegieendowment.org':
        # This named script describes the current page, unlike related cards
        # in the Next.js payload. Parse its JSON object; never execute JS.
        tag = soup.select_one('script#page-analytics-data')
        raw = tag.get_text() if tag else ''
        match = re.search(r'\bp\s*=\s*(?=\{)', raw)
        if match:
            try:
                data, _ = json.JSONDecoder().raw_decode(raw[match.end():])
                value = data.get('publication_date', '')
                if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    date = parse_date(value)
                    if date:
                        return date, 'carnegie_page_publication_date', value
            except (ValueError, AttributeError):
                pass
    if host == 'hudson.org' and '/events/' not in urlparse(url).path:
        heading = soup.find('h1')
        hero = heading.find_parent(class_='hero__content') if heading else None
        if hero:
            date, value = unique_date(hero.select('.hero__content-top .field-date time'), parse_date)
            if date:
                return date, 'hudson_article_hero_publication_date', value
    if host == 'iseas.edu.sg':
        date, value = unique_date(soup.select('meta[name="citation_publication_date"]'), parse_date)
        if date:
            return date, 'iseas_citation_publication_date', value
    if host == 'bruegel.org':
        labels = soup.select('.c-single-header__meta-row dt')
        tags = [tag.find_next_sibling('dd') for tag in labels
                if tag.get_text(' ', strip=True).casefold() == 'publishing date']
        date, value = unique_date([tag for tag in tags if tag is not None], parse_date)
        if date:
            return date, 'bruegel_visible_publication_date', value
    if host == 'merics.org':
        # Related-article dates occur elsewhere in the layout. Restrict the
        # published field to the heading's own left-hand region.
        heading = soup.find('h1')
        region = heading.find_parent(class_='layout__region--left') if heading else None
        if region:
            date, value = unique_date(region.select('.field-name-field-date-published time'), parse_date)
            if date:
                return date, 'merics_visible_publication_date', value
    if host in {'rand.org', 'fpri.org', 'rusi.org', 'hudson.org'}:
        # Citation publication metadata is explicit; citation years alone do not
        # give enough precision to establish a daily publication window.
        tag = soup.select_one('meta[name="citation_publication_date"], meta[name="citation_date"]')
        if tag and re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|' + MONTH_DATE, tag.get('content', ''), re.I):
            date = parse_date(tag.get('content', ''))
            if date:
                return date, f'{host}_citation_publication_date', tag['content']
    if host == 'fpri.org':
        date, value = unique_date(soup.select('.meta-desc-article li span'), parse_date)
        if date:
            return date, 'fpri_visible_publication_date', value
    if host == 'rand.org':
        # RAND labels the exact publication date. Never use its citation's
        # "As of" access date, copyright year or dates embedded in URL paths.
        values = []
        for text in soup.find_all(string=re.compile(r'Research Published', re.I)):
            parent = text.parent
            value = parent.get_text(' ', strip=True)
            match = re.fullmatch(r'Research Published\s+(' + MONTH_DATE + ')', value, re.I)
            if match:
                date = parse_date(match[1])
                if date:
                    values.append((date, match[1]))
        if len({d.isoformat() for d, _ in values}) == 1:
            return values[0][0], 'rand_visible_publication_date', values[0][1]
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
        selectors = 'time, [itemprop="datePublished"]'
        if host == 'hudson.org':
            selectors = '[itemprop="datePublished"], .hero__date time'
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
