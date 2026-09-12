"""Bounded recovery of thin articles from explicit publisher alternatives."""
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import config
import content_extractor as extractor
import eligibility
import http_client
import seen_ledger
from state_storage import atomic_json_write


def publisher_url(base, target):
    source = (urlparse(base).hostname or '').removeprefix('www.')
    parsed = urlparse(target)
    host = parsed.hostname or ''
    return bool(source and parsed.scheme in ('http', 'https') and
                (host == source or host.endswith('.' + source)))


def recover(item):
    """One landing-page fetch and at most two explicitly linked alternatives."""
    url = item.get('canonical_url') or item['url']
    response = http_client.get(url, headers=extractor.HEADERS, timeout=20)
    if response.status_code != 200 or not publisher_url(url, response.url):
        return {}
    if extractor.is_pdf_response(response, url, response.headers.get('content-type', '')):
        page = extractor.extract_pdf_from_response(response, url)
        return {'text': page.get('extracted_text', ''), 'evidence_url': response.url}
    soup = BeautifulSoup(response.text, 'html.parser')
    # Do not treat login/paywall text as recovered evidence.
    if any(str(node.get('isAccessibleForFree', '')).lower() == 'false'
           for node in extractor.iter_json_ld(soup) if isinstance(node, dict)):
        return {}
    text = extractor.extract_text_from_html(response.text)
    if len(text) >= config.EVIDENCE_MIN_CHARS:
        return {'text': text, 'evidence_url': response.url}
    targets = []
    for node in soup.select('meta[name="citation_pdf_url"], link[rel="amphtml"], '
                            'link[rel="alternate"], article a[href], main a[href]'):
        target = urljoin(response.url, node.get('content') or node.get('href') or '')
        label = node.get_text(' ', strip=True).lower()
        explicit = (node.name == 'meta' or 'amphtml' in node.get('rel', []) or
                    node.get('type') == 'application/pdf' or
                    urlparse(target).path.lower().endswith('.pdf') or
                    label in ('print', 'print version', 'printer-friendly version', 'full text'))
        if explicit and target != url and publisher_url(url, target) and target not in targets:
            targets.append(target)
    for target in targets[:2]:
        page = extractor.extract_page(target)
        if (page.get('paywall_detected') or page.get('is_listing_page') or
                not publisher_url(url, page.get('resolved_url') or page.get('canonical_url') or target)):
            continue
        text = page.get('extracted_text', '')
        if len(text) >= config.EVIDENCE_MIN_CHARS:
            return {'text': text, 'evidence_url': target}
    return {}


def rescue_items(items, output_dir):
    audit = dict(eligible=0, attempted=0, cache_hits=0, recovered=0, deferred=0, failed=0)
    if not config.RESCUE_MAX_ITEMS_PER_RUN:
        return audit
    ledger = seen_ledger.load_ledger(output_dir)['items']
    candidates = []
    for item in items:
        checked = dict(item)
        entry = ledger.get(seen_ledger.lookup_item_key(ledger, item), {})
        for field in ('last_reported_run_date', 'last_emailed_run_date'):
            checked[field] = entry.get(field) or item.get(field)
        if (item.get('evidence_quality') != 'insufficient' or item.get('pending_waiting') or
                item.get('paywall_detected') or not item.get('url') or eligibility.exclusion_reason(checked)):
            continue
        if item.get('topic_hints') or item.get('date_status') == 'verified_in_window':
            candidates.append(item)
    # Round-robin sources so one publisher cannot exhaust the rescue allowance.
    groups = {}
    for item in candidates:
        groups.setdefault(item.get('institution', ''), []).append(item)
    ordered = []
    while any(groups.values()):
        for group in groups.values():
            if group:
                ordered.append(group.pop(0))
    audit['eligible'] = len(ordered)
    audit['deferred'] = max(0, len(ordered) - config.RESCUE_MAX_ITEMS_PER_RUN)
    for item in ordered[:config.RESCUE_MAX_ITEMS_PER_RUN]:
        key = hashlib.sha256(json.dumps([1, item['url'], item.get('extracted_text', ''),
            item.get('summary', '')], ensure_ascii=False).encode()).hexdigest()
        path = Path(config.ENRICHMENT_CACHE_DIR) / 'rescue' / (key + '.json')
        result = None
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
            if time.time() - cached['saved_at'] < 86400 and not item.get('force_refresh'):
                result = cached['result']
                audit['cache_hits'] += 1
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if result is None:
            audit['attempted'] += 1
            try:
                result = recover(item)
                atomic_json_write(str(path), {'saved_at': time.time(), 'result': result})
            except Exception as exc:
                item['rescue_error'] = type(exc).__name__
                result = {}
        text = result.get('text', '')[:config.TEXT_STORAGE_CHAR_LIMIT]
        if len(text) >= config.EVIDENCE_MIN_CHARS:
            item.update(extracted_text=text, extracted_text_chars=len(text), evidence_quality='sufficient',
                        content_hash=extractor.content_hash_for_text(text), rescue_status='recovered',
                        rescue_evidence_url=result['evidence_url'], extraction_status='rescued')
            audit['recovered'] += 1
        else:
            item['rescue_status'] = 'insufficient'
            audit['failed'] += 1
    return audit
