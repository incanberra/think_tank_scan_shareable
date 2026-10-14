"""Shared editorial content for static email HTML and paginated PDF reports."""
import html
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, KeepTogether

import scan_runtime
from topic_badges import badges, TopicPills

SOURCE_LABELS = {
    'Center for Strategic and International Studies (CSIS)': 'CSIS',
    'Council on Foreign Relations (CFR)': 'CFR',
    'Australian Strategic Policy Institute (ASPI)': 'ASPI',
    'RAND Corporation': 'RAND',
    'International Institute for Strategic Studies (IISS)': 'IISS',
    'Institute for the Study of War (ISW)': 'ISW',
    'Royal United Services Institute (RUSI)': 'RUSI',
    'Center for a New American Security (CNAS)': 'CNAS',
    'European Council on Foreign Relations (ECFR)': 'ECFR',
    'Stockholm International Peace Research Institute (SIPRI)': 'SIPRI',
    'Foreign Policy Research Institute (FPRI)': 'FPRI',
    'National Bureau of Economic Research (NBER)': 'NBER',
    'Peterson Institute for International Economics (PIIE)': 'PIIE',
    'Mercator Institute for China Studies (MERICS)': 'MERICS',
    'Economic Research Institute for ASEAN and East Asia (ERIA)': 'ERIA',
}

def concise(text, sentences=2):
    """Select complete existing sentences; never generate additional claims."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    protected = {}
    for i, match in enumerate(re.finditer(r'\b(?:U\.S\.|U\.K\.|Dr\.|Mr\.|Ms\.|Prof\.|e\.g\.|i\.e\.)', text)):
        protected[f'ABBR{i}TOKEN'] = match.group()
    for token, value in protected.items():
        text = text.replace(value, token)
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z“"\d])', text)
    text = ' '.join(parts[:sentences])
    for token, value in protected.items():
        text = text.replace(token, value)
    return text

def display_title(item):
    title = str(item.get('short_title') or item.get('title') or '')
    title = re.sub(r'\s*\|\s*(?:The Strategist|Council on Foreign Relations|CSIS Events)$', '', title)
    return title if len(title) <= 140 else title[:137].rsplit(' ', 1)[0] + '…'

def priority(item):
    try:
        return int(item.get('importance_score') or 0)
    except (ValueError, TypeError):
        return 0


def safe_url(url):
    return str(url) if urlparse(str(url or "")).scheme in {"http", "https"} else ""


def context(data, date, audit=None):
    audit = audit or {}
    run = scan_runtime.current()
    meta = run.metadata() if run else data.get("run") or audit.get("run") or {}
    items = []
    for category, label in (("reports", "Publication"), ("events", "Event"), ("podcasts", "Podcast / video")):
        for original in data.get(category, []):
            item = dict(original)
            summary = item.get("summary", "")
            why = item.get("why_it_matters", "")
            if summary.startswith("Why it matters:\n") and "\n\nSummary:\n" in summary:
                why, summary = summary[len("Why it matters:\n"):].split("\n\nSummary:\n", 1)
            item.update(summary=concise(summary), why_it_matters=concise(why, 1),
                        display_title=display_title(item),
                        source_label=SOURCE_LABELS.get(item.get('institution'), item.get('institution', '')),
                        url=safe_url(item.get("url")), category_label=label)
            item["topic_pills"], item["hidden_topic_count"] = badges(item, limit=2)
            item['date_label'] = 'Published ' + (item.get('date') or 'Date unverified')
            if item.get('event_start_at'):
                try:
                    start = datetime.fromisoformat(item['event_start_at']).astimezone(ZoneInfo('Australia/Sydney'))
                    fmt = '%d %b %Y' if item.get('event_start_precision') == 'date' else '%d %b %Y, %H:%M %Z'
                    item['date_label'] = 'Starts ' + start.strftime(fmt)
                except (TypeError, ValueError):
                    item['date_label'] = 'Event time: see original publication'
            detail = item.get("publication_date_source_detail") or item.get("date_source", "")
            item["publication_evidence_label"] = ("Publisher publication metadata" if "metadata" in detail or "citation" in detail or detail == "page_publication"
                else "Publication date displayed by publisher" if "visible" in detail else "Publisher feed publication date" if "rss" in detail or "atom" in detail else "See publication audit")
            items.append(item)
    items.sort(key=lambda item: (item.get("editorial_tier", "main") == "further_reading", -priority(item)))
    previous_section = None
    main_index = 0
    for index, item in enumerate(items, 1):
        tier = item.get("editorial_tier", "main")
        if tier == 'further_reading':
            section = 'Further reading'
        else:
            section = 'Priority reading' if main_index < 3 else 'More analysis'
            main_index += 1
        item['anchor'] = f'article-{index}'
        item['section_heading'] = section if section != previous_section else ''
        previous_section = section
    prior = int(meta.get("prior_delivered_today", 0))
    source_rows = audit.get("source_health", {}).get("sources", [])
    source_groups = []
    for state, label in [('available', 'Available'), ('degraded', 'Partial coverage'), ('unavailable', 'Unavailable'), ('empty', 'No candidates found')]:
        names = [SOURCE_LABELS.get(r['source'], r['source']) for r in source_rows
                 if (r.get('availability') or scan_runtime.source_state(r.get('status', ''))) == state]
        if names:
            source_groups.append({'label': label, 'names': names, 'count': len(names)})
    unavailable = [SOURCE_LABELS.get(r['source'], r['source']) for r in source_rows
                   if (r.get('availability') or scan_runtime.source_state(r.get('status', ''))) == 'unavailable']
    degraded = [r["source"] for r in source_rows if (r.get('availability') or scan_runtime.source_state(r.get("status", ""))) in {"degraded", "unavailable"}]
    unknown = audit.get("enriched_candidate_summary", {}).get("date_status", {}).get("date_unknown", 0)
    selection = audit.get("review_selection", {})
    reasons = selection.get("reason_counts", {})
    pending = meta.get("pending_status_counts", {})
    analysis_metrics = data.get("analysis_metrics", {})
    cutoff = meta.get("coverage_end", "")
    if cutoff:
        cutoff = datetime.fromisoformat(cutoff).strftime("%d %b %Y, %H:%M %Z")
    headline = "Economic-security supplement" if prior else "Economic-security brief"
    if not items:
        headline = "No additional verified items" if prior else "No verified items for this edition"
    return {"date": datetime.strptime(date, "%Y-%m-%d").strftime("%A, %d %B %Y"),
            "headline": headline, "edition": "Supplement" if prior else "Daily edition", "prior": prior,
            "items": items, "count": len(items), "cutoff": cutoff, "run_id": meta.get("run_id", ""),
            "takeaways": [item for item in items if item.get('editorial_tier', 'main') != 'further_reading'][:3],
            "source_count": len(source_rows), "source_groups": source_groups, "unavailable": unavailable,
            "unknown": unknown, "degraded": degraded, "coverage_limited": bool(unknown or degraded),
            "fetched_unknown": sum(r.get("fetched_date_unknown", 0) for r in source_rows),
            "deferred": sum(r.get("pending_deferred", 0) for r in source_rows),
            "reviewed": analysis_metrics.get("sent_to_model", selection.get("selected_for_review", 0)),
            "cached": analysis_metrics.get("cache_hits", 0), "selected": selection.get("selected_for_review", 0),
            "repeats": reasons.get("skipped:already_reported", 0) + meta.get("metrics", {}).get("reported_removed_before_cap", 0),
            "pending": pending.get("pending", 0), "paused": pending.get("paused", 0),
            "discovered": selection.get("total_enriched_candidates", 0), "skipped": selection.get("skipped_before_review", 0),
            "excluded": len(data.get("excluded", [])), "needs_review": len(data.get("needs_review", []))}


def generate_html(data, date, status_notes, raw_json_str="", recall_audit=None):
    environment = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")), autoescape=select_autoescape(["html"]))
    return environment.get_template("editorial_report.html").render(**context(data, date, recall_audit))


def generate_pdf(data, date, status_notes, output_path, recall_audit=None):
    c = context(data, date, recall_audit)
    ink = colors.HexColor('#21343c')
    teal = colors.HexColor('#186a73')
    muted = colors.HexColor('#586970')
    line = colors.HexColor('#dce3e5')
    styles = {
        'brand': ParagraphStyle('brand', fontName='Helvetica-Bold', fontSize=8.5, leading=12, textColor=teal, spaceAfter=9),
        'hero': ParagraphStyle('hero', fontName='Times-Roman', fontSize=29, leading=33, textColor=ink, spaceAfter=9),
        'title': ParagraphStyle('title', fontName='Times-Roman', fontSize=16, leading=19, textColor=ink, spaceAfter=5),
        'body': ParagraphStyle('body', fontName='Helvetica', fontSize=10.5, leading=15, textColor=ink, spaceAfter=7),
        'small': ParagraphStyle('small', fontName='Helvetica', fontSize=9, leading=12, textColor=muted, spaceAfter=6),
        'foot': ParagraphStyle('foot', fontName='Helvetica', fontSize=8.5, leading=12, textColor=muted, spaceAfter=7),
        'section': ParagraphStyle('section', fontName='Helvetica-Bold', fontSize=14, leading=18, textColor=teal, spaceBefore=12, spaceAfter=9),
    }
    def p(text, style='body'):
        return Paragraph(html.escape(str(text or '')).replace('\n', '<br/>'), styles[style])
    story = [p('ECONOMIC SECURITY / THINK TANK DAILY', 'brand'), p(c['headline'], 'hero'),
             p(c['date'] + f" · {c['count']} {'item' if c['count'] == 1 else 'items'}" + (f" · {c['source_count']} sources assessed" if c['source_count'] else ''), 'small'),
             HRFlowable(width='100%', color=teal, thickness=1)]
    if c['prior']:
        story.append(p(f"{c['prior']} items delivered earlier today; this supplement contains additional verified items.", 'small'))
    if c['takeaways']:
        digest = [p('At a glance', 'section')]
        for item in c['takeaways']:
            title = html.escape(item['display_title'])
            implication = html.escape(item['why_it_matters'] or item['summary'])
            label = html.escape(item['source_label'])
            link = (' <link href="' + html.escape(item['url'], quote=True) + '" color="#186a73">' + label + '</link>') if item['url'] else ''
            digest.append(Paragraph('<b>' + title + '</b><br/>' + implication + link, styles['body']))
        story.append(KeepTogether(digest))
    if c['coverage_limited']:
        unavailable = ', '.join(c['unavailable'])
        notice = 'Coverage incomplete. ' + (unavailable + ' unavailable. ' if unavailable else '') + 'Other retrieval or date-verification gaps; details at the end.'
        story.append(p(notice, 'foot'))
    for item in c['items']:
        block = []
        if item['section_heading']:
            block.append(p(item['section_heading'], 'section'))
        block.extend([p(item['display_title'], 'title'),
                      p(item['source_label'] + ' · ' + item['category_label'] + ' · ' + item['date_label'], 'small'),
                      TopicPills(item['topic_pills'], item['hidden_topic_count']), Spacer(1, 7)])
        if item['why_it_matters']:
            block.append(Paragraph('<b>Why it matters:</b> ' + html.escape(item['why_it_matters']), styles['body']))
        block.append(p(item['summary']))
        if item['url']:
            block.append(Paragraph('<link href="' + html.escape(item['url'], quote=True) + '" color="#186a73">Read the original publication</link>', styles['small']))
        block.extend([Spacer(1, 8), HRFlowable(width='100%', color=line, thickness=.5), Spacer(1, 12)])
        # Keep the section heading, metadata and complete compact entry together.
        story.append(KeepTogether(block))
    method = p('Articles were checked for publication dates, prior reporting and relevance using retrieved source text. No candidates found does not prove a source published nothing relevant.', 'foot')
    story.append(KeepTogether([p('Coverage and method', 'section'), method]))
    for group in c['source_groups']:
        story.append(Paragraph('<b>' + html.escape(group['label']) + f" ({group['count']}):</b> " + html.escape(', '.join(group['names'])) + '.', styles['foot']))
    if c['unknown'] or c['fetched_unknown'] or c['deferred']:
        story.append(p(f"{c['unknown']} candidates have unverified dates; {c['fetched_unknown']} fetched pages lacked a verified date; {c['deferred']} await a scheduled retry.", 'foot'))
    story.append(p('Coverage ends: ' + (c['cutoff'] or 'see run audit') + '. Sources recovering from a gap may have longer lookback windows.', 'foot'))
    story.append(p(f"Audit: {c['discovered']} candidates assessed; {c['selected']} selected; {c['reviewed']} model reviews; {c['cached']} cached decisions; {c['excluded']} excluded; {c['needs_review']} need review. Queue: {c['pending']} pending, {c['paused']} paused. {c['repeats']} previously reported candidates removed.", 'foot'))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    doc = SimpleDocTemplate(output_path, pagesize=A4, rightMargin=44, leftMargin=44, topMargin=43, bottomMargin=42,
                           title='Economic-security brief', author='Think Tank Scanner')
    def page_frame(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(teal)
        canvas.setLineWidth(2)
        canvas.line(44, A4[1] - 27, A4[0] - 44, A4[1] - 27)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(muted)
        canvas.drawString(44, 23, 'Economic security · ' + c['date'] + ' · ' + c['edition'])
        canvas.drawRightString(A4[0] - 44, 23, str(document.page))
        canvas.restoreState()
    doc.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
