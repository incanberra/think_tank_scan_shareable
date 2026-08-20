import os
import json
from datetime import datetime
from xml.sax.saxutils import escape
from jinja2 import Template
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
import config

METHODOLOGY_TEXT = (
    "Reports were discovered through source-specific RSS feeds, source-native sitemaps, "
    "source index pages, and configured unpaid fallback search where enabled. Candidates "
    "were enriched with page-level metadata, canonical URLs, extracted HTML/PDF text, "
    "topic hints, persistent first-seen ledger checks, publication-date cross-checks, "
    "and content-change hashes. Items sent to the model were reviewed against the "
    "configured economic-security topic ontology and included only where the evidence "
    "showed material relevance within the coverage window or where date uncertainty "
    "required analyst attention. Raw candidates, enriched candidates, recall risks, "
    "exclusions, review decisions, source-health data, and ledger state are saved to "
    "audit files."
)


def clean_output_text(value):
    text = str(value or "")
    return "".join(ch for ch in text if ch in "\n\r\t" or ord(ch) >= 32)


def sanitize_report_data(value):
    if isinstance(value, dict):
        return {key: sanitize_report_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_report_data(item) for item in value]
    if isinstance(value, str):
        return clean_output_text(value)
    return value

# NumberedCanvas helper for PDF page numbers
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#555555"))
        
        # Header (Top of page)
        self.drawString(inch, 10.5 * inch, "Economic Security Think Tank Scan")
        self.drawRightString(7.5 * inch, 10.5 * inch, datetime.now().strftime("%d %B %Y"))
        self.setStrokeColor(colors.HexColor("#dddddd"))
        self.setLineWidth(0.5)
        self.line(inch, 10.4 * inch, 7.5 * inch, 10.4 * inch)
        
        # Footer (Bottom of page)
        self.line(inch, 0.75 * inch, 7.5 * inch, 0.75 * inch)
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(7.5 * inch, 0.55 * inch, page_text)
        self.restoreState()

def get_stars_and_label(score):
    """
    Returns the stars and label for an importance score.
    """
    return config.IMPORTANCE_RATINGS.get(score, ("***", "Useful"))

def get_badge_markdown(topic):
    """
    Returns the markdown text representation for a topic badge.
    """
    badge_info = config.TOPIC_BADGE_CONFIGS.get(topic)
    if badge_info:
        return badge_info[0] # Returns '🟢 Critical Minerals', '🔵 Economic Coercion', etc.
    return topic

def format_review_metadata(item):
    """
    Formats v3 review provenance for Markdown reports.
    """
    parts = []
    if item.get("relevance_confidence"):
        parts.append(f"Confidence: {item['relevance_confidence']}")
    if item.get("date_status"):
        parts.append(f"Date check: {item['date_status']}")
    if item.get("first_seen_run_date"):
        parts.append(f"First seen: {item['first_seen_run_date']}")
    if item.get("date_crosscheck_status"):
        parts.append(f"Seen check: {item['date_crosscheck_status']}")
    if item.get("extraction_status"):
        parts.append(f"Extraction: {item['extraction_status']}")
    methods = item.get("discovery_methods") or []
    if methods:
        parts.append(f"Discovery: {', '.join(methods)}")
    return " | ".join(parts)

def get_recall_risk_details(recall_audit):
    if not recall_audit:
        return []
    return recall_audit.get("recall_risk_details") or []

def get_recall_risk_flags(recall_audit):
    if not recall_audit:
        return []
    return recall_audit.get("recall_risk_flags") or []

def format_recall_risk_markdown(recall_audit):
    """
    Formats daily recall risks for Markdown reports.
    """
    flags = get_recall_risk_flags(recall_audit)
    details = get_recall_risk_details(recall_audit)
    if not flags and not details:
        return "No material recall risks flagged by the scanner audit."

    lines = []
    if flags:
        lines.append("**Summary flags**")
        lines.extend([f"- {flag}" for flag in flags])
        lines.append("")
    if details:
        lines.append("**Details**")
        for detail in details[:20]:
            severity = detail.get("severity", "unknown").upper()
            area = detail.get("area", "General")
            issue = detail.get("issue", "")
            status = detail.get("status", "")
            implication = detail.get("implication", "")
            status_part = f" Status: {status}" if status else ""
            lines.append(f"- **{severity} - {area}:** {issue}.{status_part} {implication}".strip())
    return "\n".join(lines)


def all_report_items(analyzed_data):
    return (
        list(analyzed_data.get("reports", []))
        + list(analyzed_data.get("podcasts", []))
        + list(analyzed_data.get("events", []))
    )


def build_executive_summary(analyzed_data, recall_audit=None):
    items = all_report_items(analyzed_data)
    top_items = sorted(
        items,
        key=lambda item: (item.get("importance_score", 0), item.get("relevance_confidence", "")),
        reverse=True,
    )[:5]
    return {
        "reports": len(analyzed_data.get("reports", [])),
        "podcasts": len(analyzed_data.get("podcasts", [])),
        "events": len(analyzed_data.get("events", [])),
        "total": len(items),
        "top_items": top_items,
        "recall_flags": get_recall_risk_flags(recall_audit),
    }


def format_executive_summary_markdown(analyzed_data, recall_audit=None):
    summary = build_executive_summary(analyzed_data, recall_audit)
    lines = [
        f"- **Items included:** {summary['total']} total "
        f"({summary['reports']} publications, {summary['podcasts']} podcasts/videos, {summary['events']} events)."
    ]
    if summary["top_items"]:
        lines.append("- **Highest-priority items:**")
        for item in summary["top_items"]:
            stars, label = get_stars_and_label(item.get("importance_score", 3))
            lines.append(f"  - {stars} {label}: {item.get('institution')} - {item.get('title')}")
    else:
        lines.append("- **Highest-priority items:** No qualifying items found.")
    if summary["recall_flags"]:
        lines.append("- **Recall watch:** " + "; ".join(summary["recall_flags"][:3]))
    else:
        lines.append("- **Recall watch:** No material recall risks flagged.")
    return "\n".join(lines)


def build_scan_quality(analyzed_data, recall_audit=None):
    recall_audit = recall_audit or {}
    raw_summary = recall_audit.get("raw_candidate_summary", {})
    enriched_summary = recall_audit.get("enriched_candidate_summary", {})
    review_selection = recall_audit.get("review_selection", {})
    seen_ledger = recall_audit.get("seen_ledger", {})
    reason_counts = review_selection.get("reason_counts") or review_selection.get("review_selection_reason_counts", {})
    included_count = (
        len(analyzed_data.get("reports", []))
        + len(analyzed_data.get("podcasts", []))
        + len(analyzed_data.get("events", []))
    )
    audit_counts = analyzed_data.get("audit", {})
    excluded_count = len(analyzed_data.get("excluded", []))
    if not excluded_count:
        excluded_count = audit_counts.get("excluded_count", 0)
    needs_review_count = len(analyzed_data.get("needs_review", []))
    if not needs_review_count:
        needs_review_count = audit_counts.get("needs_review_count", 0)
    sent_to_model = review_selection.get("selected_for_review", 0)
    if not sent_to_model:
        sent_to_model = included_count + excluded_count + needs_review_count
    return {
        "raw_candidates": raw_summary.get("total_candidates", 0),
        "enriched_candidates": enriched_summary.get("total_candidates", 0),
        "sent_to_model": sent_to_model,
        "included_items": included_count,
        "excluded_items": excluded_count,
        "new_this_run": seen_ledger.get("new_this_run", 0),
        "seen_before": seen_ledger.get("seen_before", 0),
        "undated_seen_before_skipped": reason_counts.get("skipped:date_unknown_seen_before_skipped", 0),
        "content_changed_reviewed": reason_counts.get("selected:date_unknown_seen_before_content_changed", 0),
        "ledger_path": seen_ledger.get("seen_ledger_path", ""),
        "ledger_backfill": seen_ledger.get("backfill", {}),
    }


def format_scan_quality_markdown(analyzed_data, recall_audit=None):
    quality = build_scan_quality(analyzed_data, recall_audit)
    backfill = quality.get("ledger_backfill") or {}
    backfill_text = "not recorded"
    if backfill.get("completed_at"):
        backfill_text = (
            f"completed {backfill.get('completed_at')} "
            f"from {backfill.get('files_processed', 0)} audit files"
        )
    lines = [
        f"- **Raw candidates found:** {quality['raw_candidates']}",
        f"- **Candidates enriched:** {quality['enriched_candidates']}",
        f"- **Candidates sent to model:** {quality['sent_to_model']}",
        f"- **Included items:** {quality['included_items']}",
        f"- **Excluded by model:** {quality['excluded_items']}",
        f"- **Ledger:** {quality['new_this_run']} new, {quality['seen_before']} seen before",
        f"- **Undated seen-before skipped:** {quality['undated_seen_before_skipped']}",
        f"- **Undated seen-before changed and reviewed:** {quality['content_changed_reviewed']}",
        f"- **Backfill status:** {backfill_text}",
    ]
    return "\n".join(lines)


def truncate_text(text, max_chars=260):
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def format_evidence_compact(evidence, max_points=2):
    points = [truncate_text(point, 180) for point in (evidence or [])[:max_points] if point]
    return "; ".join(points)


def get_badge_html(topic):
    """
    Returns the HTML representation for a topic badge with inline CSS.
    """
    badge_info = config.TOPIC_BADGE_CONFIGS.get(topic)
    if badge_info:
        label, bg, text = badge_info
        return f'<span style="background-color: {bg}; color: {text}; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; margin-right: 6px; display: inline-block; border: 1px solid rgba(0,0,0,0.05);">{label}</span>'
    return f'<span style="background-color: #f2f2f2; color: #555555; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; margin-right: 6px; display: inline-block;">{topic}</span>'

def format_markdown_card(item, category_label="Date"):
    """
    Formats a single card in Markdown syntax matching the Master Markdown template.
    """
    stars, rating_label = get_stars_and_label(item['importance_score'])
    badges = ", ".join([get_badge_markdown(t) for t in item['tags']])
    
    date_field = f"{category_label}: {item['date']}"
    if category_label == "Date" and item.get("event_time"):
        date_field += f" ({item['event_time']})"
        
    author_label = "Authors" if category_label == "Date" else "Speakers/Hosts"
    review_metadata = format_review_metadata(item)
    review_lines = ""
    if review_metadata:
        review_lines = f"\nReview: {review_metadata}\n"

    evidence_lines = ""
    if item.get("evidence"):
        evidence = format_evidence_compact(item["evidence"])
        evidence_lines = f"\nEvidence checked: {evidence}\n"
    
    card = f"""**{item['institution']}**
### {item['title']}

{date_field}

{author_label}: {item['author']}

Topics: {badges}

Importance: {stars} - {rating_label}
{review_lines}

**Why it matters**
*{item.get('why_it_matters', 'N/A')}*
{evidence_lines}
**Summary**
{item['summary']}

**Link**
[{item['url']}]({item['url']})
"""
    return card

def generate_markdown(analyzed_data, run_date_str, status_notes, recall_audit=None):
    """
    Generates the Markdown report by populating the master template.
    """
    analyzed_data = sanitize_report_data(analyzed_data)
    status_notes = sanitize_report_data(status_notes)
    recall_audit = sanitize_report_data(recall_audit)
    # 1. New Publications Cards
    if analyzed_data["reports"]:
        pub_cards = "\n\n---\n\n".join([format_markdown_card(item, "Date") for item in analyzed_data["reports"]])
    else:
        pub_cards = "*No qualifying items found in this coverage window.*"
        
    # 2. Podcast & Video Cards
    if analyzed_data["podcasts"]:
        podcast_cards = "\n\n---\n\n".join([format_markdown_card(item, "Date") for item in analyzed_data["podcasts"]])
    else:
        podcast_cards = "*No qualifying items found in this coverage window.*"
        
    # 3. Event Cards
    if analyzed_data["events"]:
        event_cards = "\n\n---\n\n".join([format_markdown_card(item, "Event Date") for item in analyzed_data["events"]])
    else:
        event_cards = "*No qualifying items found in this coverage window.*"
        
    # 4. Sources Checked
    sources_lines = []
    # Sort checked list alphabetically
    for inst in sorted(config.THINK_TANKS):
        status = status_notes.get(inst, "checked/no qualifying items")
        sources_lines.append(f"- **{inst}**: {status}")
    sources_checked = "\n".join(sources_lines)
    recall_risks = format_recall_risk_markdown(recall_audit)
    executive_summary = format_executive_summary_markdown(analyzed_data, recall_audit)
    scan_quality = format_scan_quality_markdown(analyzed_data, recall_audit)
    
    # Master template
    template_str = """# Economic Security Think Tank Scan

**Date:** {{run_date}}

**Coverage Window:** {{coverage_window}}

---

## Executive Summary

{{executive_summary}}

---

## Scan Quality

{{scan_quality}}

---

## New Publications

{{publication_cards}}

---

## Podcasts & Videos

{{podcast_cards}}

---

## Upcoming Events

{{event_cards}}

---

## Recall Risks

{{recall_risks}}

---

## Sources Checked

{{sources_checked}}

---

### Methodology

{{methodology_text}}
"""
    
    # Parse template
    t = Template(template_str)
    markdown_output = t.render(
        run_date=run_date_str,
        coverage_window="48 hours",
        executive_summary=executive_summary,
        scan_quality=scan_quality,
        publication_cards=pub_cards,
        podcast_cards=podcast_cards,
        event_cards=event_cards,
        recall_risks=recall_risks,
        sources_checked=sources_checked,
        methodology_text=METHODOLOGY_TEXT,
    )
    
    return markdown_output

def generate_html(analyzed_data, run_date_str, status_notes, raw_json_str="", recall_audit=None):
    """
    Generates the HTML email body with inline CSS.
    """
    analyzed_data = sanitize_report_data(analyzed_data)
    status_notes = sanitize_report_data(status_notes)
    recall_audit = sanitize_report_data(recall_audit)
    html_template = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Economic Security Think Tank Scan</title>
</head>
<body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f5f6f8; color: #333333; margin: 0; padding: 20px; -webkit-font-smoothing: antialiased;">
    <div style="max-width: 800px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05); overflow: hidden; border: 1px solid #e1e4e8;">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #1f4e78 0%, #0f253d 100%); padding: 30px; color: #ffffff; text-align: left;">
            <h1 style="margin: 0; font-size: 24px; font-weight: bold; letter-spacing: -0.5px;">Economic Security Think Tank Scan</h1>
            <p style="margin: 10px 0 0 0; font-size: 14px; opacity: 0.85;">
                <strong>Date:</strong> {{run_date}} &nbsp;&nbsp;|&nbsp;&nbsp; <strong>Coverage:</strong> 48 Hours
            </p>
        </div>
        
        <!-- Content -->
        <div style="padding: 30px;">

            <!-- Executive Summary -->
            <h2 style="font-size: 18px; color: #0f253d; border-bottom: 2px solid #d9e8f5; padding-bottom: 8px; margin-top: 0; margin-bottom: 16px;">Executive Summary</h2>
            <div style="background-color: #f6f9fc; border: 1px solid #d9e8f5; border-radius: 4px; padding: 16px; margin-bottom: 26px; font-size: 13px; line-height: 1.5;">
                <p style="margin: 0 0 10px 0;"><strong>Items included:</strong> {{executive.total}} total ({{executive.reports}} publications, {{executive.podcasts}} podcasts/videos, {{executive.events}} events).</p>
                {% if executive.top_items %}
                    <p style="margin: 0 0 8px 0;"><strong>Highest-priority items:</strong></p>
                    <ol style="margin: 0 0 10px 20px; padding: 0;">
                    {% for top in executive.top_items %}
                        <li style="margin-bottom: 4px;"><strong>{{top.institution}}</strong>: {{top.title}}</li>
                    {% endfor %}
                    </ol>
                {% else %}
                    <p style="margin: 0 0 10px 0;"><strong>Highest-priority items:</strong> No qualifying items found.</p>
                {% endif %}
                {% if executive.recall_flags %}
                    <p style="margin: 0;"><strong>Recall watch:</strong> {{executive.recall_flags[:3] | join("; ")}}</p>
                {% else %}
                    <p style="margin: 0;"><strong>Recall watch:</strong> No material recall risks flagged.</p>
                {% endif %}
            </div>

            <!-- Scan Quality -->
            <h2 style="font-size: 18px; color: #315a3c; border-bottom: 2px solid #dcebdd; padding-bottom: 8px; margin-top: 0; margin-bottom: 16px;">Scan Quality</h2>
            <div style="background-color: #f7fbf7; border: 1px solid #dcebdd; border-radius: 4px; padding: 16px; margin-bottom: 26px; font-size: 13px; line-height: 1.6;">
                <p style="margin: 0 0 8px 0;"><strong>Discovery:</strong> {{scan_quality.raw_candidates}} raw candidates; {{scan_quality.enriched_candidates}} enriched; {{scan_quality.sent_to_model}} sent to model.</p>
                <p style="margin: 0 0 8px 0;"><strong>Model outcome:</strong> {{scan_quality.included_items}} included; {{scan_quality.excluded_items}} excluded.</p>
                <p style="margin: 0 0 8px 0;"><strong>Ledger:</strong> {{scan_quality.new_this_run}} new; {{scan_quality.seen_before}} seen before; {{scan_quality.undated_seen_before_skipped}} undated seen-before skipped.</p>
                <p style="margin: 0;"><strong>Changed undated pages reviewed:</strong> {{scan_quality.content_changed_reviewed}}</p>
            </div>
            
            <!-- Publications -->
            <h2 style="font-size: 18px; color: #1f4e78; border-bottom: 2px solid #ddebf7; padding-bottom: 8px; margin-top: 0; margin-bottom: 20px;">New Publications</h2>
            {% if reports %}
                {% for item in reports %}
                    <div style="background-color: #fafbfd; border: 1px solid #e1e4e8; border-left: 4px solid #1f4e78; border-radius: 4px; padding: 20px; margin-bottom: 20px;">
                        <div style="font-size: 12px; font-weight: bold; color: #666666; text-transform: uppercase; margin-bottom: 5px;">{{item.institution}}</div>
                        <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #0f253d;"><a href="{{item.url}}" style="color: #0f253d; text-decoration: none; border-bottom: 1px dotted #1f4e78;">{{item.title}}</a></h3>
                        <div style="font-size: 13px; color: #555555; margin-bottom: 12px;">
                            <strong>Date:</strong> {{item.date}} &nbsp;&nbsp;|&nbsp;&nbsp; <strong>Authors:</strong> {{item.author}}
                        </div>
                        <div style="margin-bottom: 12px;">
                            <strong>Topics:</strong> {{item.topic_badges_html}}
                        </div>
                        <div style="font-size: 13px; margin-bottom: 12px;">
                            <strong>Importance:</strong> <span style="color: #ffc107; font-weight: bold;">{{item.stars}}</span> - <span style="font-weight: bold;">{{item.rating_label}}</span>
                        </div>
                        {% if item.relevance_confidence or item.date_status or item.extraction_status %}
                        <div style="font-size: 12px; color: #555555; margin-bottom: 12px;">
                            <strong>Review:</strong>
                            {% if item.relevance_confidence %}Confidence: {{item.relevance_confidence}}{% endif %}
                            {% if item.date_status %} | Date: {{item.date_status}}{% endif %}
                            {% if item.extraction_status %} | Extraction: {{item.extraction_status}}{% endif %}
                        </div>
                        {% endif %}
                        {% if item.evidence_compact %}
                        <p style="font-size: 12px; color: #555555; margin: 0 0 12px 0;"><strong>Evidence checked:</strong> {{item.evidence_compact}}</p>
                        {% endif %}
                        <div style="background-color: #ffffff; border: 1px solid #f0f1f4; border-radius: 4px; padding: 12px; margin-bottom: 12px; font-size: 13px; border-left: 3px solid #ffc107;">
                            <strong>Why it matters:</strong> <em style="color: #444444;">{{item.why_it_matters}}</em>
                        </div>
                        <p style="font-size: 13px; line-height: 1.5; color: #444444; margin: 0 0 12px 0;">
                            {{item.summary}}
                        </p>
                        <div style="font-size: 13px;">
                            <a href="{{item.url}}" style="color: #1f4e78; font-weight: bold; text-decoration: none;">View Publication &rarr;</a>
                        </div>
                    </div>
                {% endfor %}
            {% else %}
                <p style="font-size: 14px; color: #666666; font-style: italic; margin-bottom: 30px;">No qualifying items found in this coverage window.</p>
            {% endif %}
            
            <!-- Podcasts & Videos -->
            <h2 style="font-size: 18px; color: #7030a0; border-bottom: 2px solid #f2eedf; padding-bottom: 8px; margin-top: 30px; margin-bottom: 20px;">Podcasts & Videos</h2>
            {% if podcasts %}
                {% for item in podcasts %}
                    <div style="background-color: #fafbfd; border: 1px solid #e1e4e8; border-left: 4px solid #7030a0; border-radius: 4px; padding: 20px; margin-bottom: 20px;">
                        <div style="font-size: 12px; font-weight: bold; color: #666666; text-transform: uppercase; margin-bottom: 5px;">{{item.institution}}</div>
                        <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #0f253d;"><a href="{{item.url}}" style="color: #0f253d; text-decoration: none; border-bottom: 1px dotted #7030a0;">{{item.title}}</a></h3>
                        <div style="font-size: 13px; color: #555555; margin-bottom: 12px;">
                            <strong>Date:</strong> {{item.date}} &nbsp;&nbsp;|&nbsp;&nbsp; <strong>Speakers/Hosts:</strong> {{item.author}}
                        </div>
                        <div style="margin-bottom: 12px;">
                            <strong>Topics:</strong> {{item.topic_badges_html}}
                        </div>
                        <div style="font-size: 13px; margin-bottom: 12px;">
                            <strong>Importance:</strong> <span style="color: #ffc107; font-weight: bold;">{{item.stars}}</span> - <span style="font-weight: bold;">{{item.rating_label}}</span>
                        </div>
                        {% if item.relevance_confidence or item.date_status or item.extraction_status %}
                        <div style="font-size: 12px; color: #555555; margin-bottom: 12px;">
                            <strong>Review:</strong>
                            {% if item.relevance_confidence %}Confidence: {{item.relevance_confidence}}{% endif %}
                            {% if item.date_status %} | Date: {{item.date_status}}{% endif %}
                            {% if item.extraction_status %} | Extraction: {{item.extraction_status}}{% endif %}
                        </div>
                        {% endif %}
                        {% if item.evidence_compact %}
                        <p style="font-size: 12px; color: #555555; margin: 0 0 12px 0;"><strong>Evidence checked:</strong> {{item.evidence_compact}}</p>
                        {% endif %}
                        <div style="background-color: #ffffff; border: 1px solid #f0f1f4; border-radius: 4px; padding: 12px; margin-bottom: 12px; font-size: 13px; border-left: 3px solid #ffc107;">
                            <strong>Why it matters:</strong> <em style="color: #444444;">{{item.why_it_matters}}</em>
                        </div>
                        <p style="font-size: 13px; line-height: 1.5; color: #444444; margin: 0 0 12px 0;">
                            {{item.summary}}
                        </p>
                        <div style="font-size: 13px;">
                            <a href="{{item.url}}" style="color: #7030a0; font-weight: bold; text-decoration: none;">Watch / Listen &rarr;</a>
                        </div>
                    </div>
                {% endfor %}
            {% else %}
                <p style="font-size: 14px; color: #666666; font-style: italic; margin-bottom: 30px;">No qualifying items found in this coverage window.</p>
            {% endif %}
            
            <!-- Upcoming Events -->
            <h2 style="font-size: 18px; color: #c65911; border-bottom: 2px solid #fce4d6; padding-bottom: 8px; margin-top: 30px; margin-bottom: 20px;">Upcoming Events</h2>
            {% if events %}
                {% for item in events %}
                    <div style="background-color: #fafbfd; border: 1px solid #e1e4e8; border-left: 4px solid #c65911; border-radius: 4px; padding: 20px; margin-bottom: 20px;">
                        <div style="font-size: 12px; font-weight: bold; color: #666666; text-transform: uppercase; margin-bottom: 5px;">{{item.institution}}</div>
                        <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #0f253d;"><a href="{{item.url}}" style="color: #0f253d; text-decoration: none; border-bottom: 1px dotted #c65911;">{{item.title}}</a></h3>
                        <div style="font-size: 13px; color: #555555; margin-bottom: 12px;">
                            <strong>Event Date:</strong> {{item.date}} {% if item.event_time %}({{item.event_time}}){% endif %} &nbsp;&nbsp;|&nbsp;&nbsp; <strong>Speakers:</strong> {{item.author}}
                        </div>
                        <div style="margin-bottom: 12px;">
                            <strong>Topics:</strong> {{item.topic_badges_html}}
                        </div>
                        <div style="font-size: 13px; margin-bottom: 12px;">
                            <strong>Importance:</strong> <span style="color: #ffc107; font-weight: bold;">{{item.stars}}</span> - <span style="font-weight: bold;">{{item.rating_label}}</span>
                        </div>
                        {% if item.relevance_confidence or item.date_status or item.extraction_status %}
                        <div style="font-size: 12px; color: #555555; margin-bottom: 12px;">
                            <strong>Review:</strong>
                            {% if item.relevance_confidence %}Confidence: {{item.relevance_confidence}}{% endif %}
                            {% if item.date_status %} | Date: {{item.date_status}}{% endif %}
                            {% if item.extraction_status %} | Extraction: {{item.extraction_status}}{% endif %}
                        </div>
                        {% endif %}
                        {% if item.evidence_compact %}
                        <p style="font-size: 12px; color: #555555; margin: 0 0 12px 0;"><strong>Evidence checked:</strong> {{item.evidence_compact}}</p>
                        {% endif %}
                        <div style="background-color: #ffffff; border: 1px solid #f0f1f4; border-radius: 4px; padding: 12px; margin-bottom: 12px; font-size: 13px; border-left: 3px solid #ffc107;">
                            <strong>Why it matters:</strong> <em style="color: #444444;">{{item.why_it_matters}}</em>
                        </div>
                        <p style="font-size: 13px; line-height: 1.5; color: #444444; margin: 0 0 12px 0;">
                            {{item.summary}}
                        </p>
                        <div style="font-size: 13px;">
                            <a href="{{item.url}}" style="color: #c65911; font-weight: bold; text-decoration: none;">Register / Join &rarr;</a>
                        </div>
                    </div>
                {% endfor %}
            {% else %}
                <p style="font-size: 14px; color: #666666; font-style: italic; margin-bottom: 30px;">No qualifying items found in this coverage window.</p>
            {% endif %}
            
            <!-- Recall Risks -->
            <h2 style="font-size: 18px; color: #8a4b08; border-bottom: 2px solid #f8e0b4; padding-bottom: 8px; margin-top: 40px; margin-bottom: 20px;">Recall Risks</h2>
            {% if recall_flags or recall_details %}
                {% if recall_flags %}
                <ul style="font-size: 13px; line-height: 1.6; color: #5f3b00; padding-left: 20px; margin: 0 0 16px 0;">
                    {% for flag in recall_flags %}
                        <li>{{flag}}</li>
                    {% endfor %}
                </ul>
                {% endif %}
                {% if recall_details %}
                    {% for risk in recall_details[:20] %}
                    <div style="border: 1px solid #f1d59f; background-color: #fffaf0; border-radius: 4px; padding: 12px; margin-bottom: 10px; font-size: 13px; color: #4a3412;">
                        <strong>{{risk.severity | upper}} - {{risk.area}}:</strong> {{risk.issue}}
                        {% if risk.status %}<br><span style="color: #6b5b44;">Status: {{risk.status}}</span>{% endif %}
                        {% if risk.implication %}<br><span style="color: #6b5b44;">{{risk.implication}}</span>{% endif %}
                    </div>
                    {% endfor %}
                {% endif %}
            {% else %}
                <p style="font-size: 14px; color: #666666; font-style: italic; margin-bottom: 30px;">No material recall risks flagged by the scanner audit.</p>
            {% endif %}

            <!-- Sources Checked -->
            <h2 style="font-size: 18px; color: #333333; border-bottom: 2px solid #ededed; padding-bottom: 8px; margin-top: 40px; margin-bottom: 20px;">Sources Checked</h2>
            <ul style="font-size: 13px; line-height: 1.6; color: #555555; padding-left: 20px; margin: 0 0 30px 0;">
                {% for inst, status in checked_sources %}
                    <li><strong>{{inst}}</strong>: <span style="font-style: italic; color: #666666;">{{status}}</span></li>
                {% endfor %}
            </ul>
            
            <!-- Methodology -->
            <div style="background-color: #ededed; border-radius: 4px; padding: 20px; font-size: 12px; line-height: 1.5; color: #666666;">
                <h4 style="margin: 0 0 8px 0; color: #333333; font-size: 13px;">Methodology</h4>
                {{methodology_text}}
            </div>
            
            {% if raw_json %}
            <!-- Collapsed JSON Appendix -->
            <div style="margin-top: 30px; border-top: 1px solid #e1e4e8; padding-top: 20px;">
                <details style="cursor: pointer; font-size: 13px; color: #666666;">
                    <summary style="font-weight: bold; color: #1f4e78; padding: 5px 0;">View Structured JSON Appendix</summary>
                    <pre style="background-color: #272822; color: #f8f8f2; padding: 15px; border-radius: 4px; overflow-x: auto; font-family: Consolas, Monaco, monospace; font-size: 11px; margin-top: 10px;">{{raw_json}}</pre>
                </details>
            </div>
            {% endif %}
            
        </div>
        
        <!-- Footer -->
        <div style="background-color: #0f253d; padding: 20px; color: #ffffff; text-align: center; font-size: 12px; opacity: 0.9;">
            This scan is an automated product.
        </div>
        
    </div>
</body>
</html>
"""
    # Pre-render some values for easy rendering
    prepped_reports = []
    for r in analyzed_data["reports"]:
        item = dict(r)
        stars, label = get_stars_and_label(item["importance_score"])
        item["stars"] = stars
        item["rating_label"] = label
        item["topic_badges_html"] = " ".join([get_badge_html(t) for t in item["tags"]])
        item["evidence_compact"] = format_evidence_compact(item.get("evidence"))
        prepped_reports.append(item)
        
    prepped_podcasts = []
    for p in analyzed_data["podcasts"]:
        item = dict(p)
        stars, label = get_stars_and_label(item["importance_score"])
        item["stars"] = stars
        item["rating_label"] = label
        item["topic_badges_html"] = " ".join([get_badge_html(t) for t in item["tags"]])
        item["evidence_compact"] = format_evidence_compact(item.get("evidence"))
        prepped_podcasts.append(item)
        
    prepped_events = []
    for e in analyzed_data["events"]:
        item = dict(e)
        stars, label = get_stars_and_label(item["importance_score"])
        item["stars"] = stars
        item["rating_label"] = label
        item["topic_badges_html"] = " ".join([get_badge_html(t) for t in item["tags"]])
        item["evidence_compact"] = format_evidence_compact(item.get("evidence"))
        prepped_events.append(item)
        
    checked_sources_list = sorted(status_notes.items())
    recall_flags = get_recall_risk_flags(recall_audit)
    recall_details = get_recall_risk_details(recall_audit)
    executive = build_executive_summary(analyzed_data, recall_audit)
    scan_quality = build_scan_quality(analyzed_data, recall_audit)
    
    t = Template(html_template)
    html_output = t.render(
        run_date=run_date_str,
        reports=prepped_reports,
        podcasts=prepped_podcasts,
        events=prepped_events,
        checked_sources=checked_sources_list,
        raw_json=raw_json_str,
        recall_flags=recall_flags,
        recall_details=recall_details,
        executive=executive,
        scan_quality=scan_quality,
        methodology_text=METHODOLOGY_TEXT,
    )
    return html_output

def generate_pdf(analyzed_data, run_date_str, status_notes, output_path, recall_audit=None):
    """
    Generates a professional PDF version of the scan report.
    """
    analyzed_data = sanitize_report_data(analyzed_data)
    status_notes = sanitize_report_data(status_notes)
    recall_audit = sanitize_report_data(recall_audit)
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=inch,
        leftMargin=inch,
        topMargin=1.1 * inch, # Space for header
        bottomMargin=0.9 * inch
    )
    
    styles = getSampleStyleSheet()
    
    # Custom Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#0f253d"),
        spaceAfter=15
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#555555"),
        spaceAfter=20
    )
    
    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#1f4e78"),
        spaceBefore=15,
        spaceAfter=10,
        keepWithNext=True
    )
    
    card_institution = ParagraphStyle(
        'CardInst',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#555555"),
        spaceAfter=4
    )
    
    card_title = ParagraphStyle(
        'CardTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0f253d"),
        spaceAfter=6
    )
    
    card_meta = ParagraphStyle(
        'CardMeta',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#444444"),
        spaceAfter=6
    )
    
    card_importance = ParagraphStyle(
        'CardImportance',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#111111"),
        spaceAfter=6
    )
    
    card_why = ParagraphStyle(
        'CardWhy',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#333333"),
        spaceAfter=6
    )
    
    card_summary = ParagraphStyle(
        'CardSummary',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#333333"),
        spaceAfter=6
    )
    
    card_link = ParagraphStyle(
        'CardLink',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1f4e78")
    )
    
    source_checked_style = ParagraphStyle(
        'SourceChecked',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#444444")
    )
    
    methodology_style = ParagraphStyle(
        'Methodology',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11.5,
        textColor=colors.HexColor("#666666")
    )
    
    story = []
    
    # Title & Metadata
    story.append(Paragraph("Economic Security Think Tank Scan", title_style))
    story.append(Paragraph(f"Date: {run_date_str}  |  Coverage Window: 48 Hours  |  Canberra Daily Run (3:00 AM)", subtitle_style))
    story.append(Spacer(1, 10))
    executive = build_executive_summary(analyzed_data, recall_audit)
    story.append(Paragraph("Executive Summary", section_heading))
    story.append(
        Paragraph(
            f"<b>Items included:</b> {executive['total']} total "
            f"({executive['reports']} publications, {executive['podcasts']} podcasts/videos, {executive['events']} events).",
            source_checked_style,
        )
    )
    if executive["top_items"]:
        story.append(Paragraph("<b>Highest-priority items:</b>", source_checked_style))
        for top in executive["top_items"]:
            stars, label = get_stars_and_label(top.get("importance_score", 3))
            story.append(
                Paragraph(
                    f"{escape(stars)} {escape(label)}: <b>{escape(str(top.get('institution', '')))}</b> - "
                    f"{escape(str(top.get('title', '')))}",
                    source_checked_style,
                )
            )
    else:
        story.append(Paragraph("<b>Highest-priority items:</b> No qualifying items found.", source_checked_style))
    if executive["recall_flags"]:
        story.append(
            Paragraph(
                f"<b>Recall watch:</b> {escape('; '.join(executive['recall_flags'][:3]))}",
                source_checked_style,
            )
        )
    else:
        story.append(Paragraph("<b>Recall watch:</b> No material recall risks flagged.", source_checked_style))
    scan_quality = build_scan_quality(analyzed_data, recall_audit)
    story.append(Spacer(1, 8))
    story.append(Paragraph("Scan Quality", section_heading))
    scan_quality_rows = [
        ["Raw candidates", scan_quality["raw_candidates"], "Enriched", scan_quality["enriched_candidates"]],
        ["Sent to model", scan_quality["sent_to_model"], "Included", scan_quality["included_items"]],
        ["Excluded", scan_quality["excluded_items"], "New / seen before", f"{scan_quality['new_this_run']} / {scan_quality['seen_before']}"],
        ["Undated seen-before skipped", scan_quality["undated_seen_before_skipped"], "Changed undated reviewed", scan_quality["content_changed_reviewed"]],
    ]
    scan_table = Table(scan_quality_rows, colWidths=[2.0 * inch, 1.1 * inch, 2.0 * inch, 1.1 * inch])
    scan_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f7fbf7")),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#dcebdd")),
        ('INNERGRID', (0,0), (-1,-1), 0.25, colors.HexColor("#dcebdd")),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 8.5),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(scan_table)
    story.append(Spacer(1, 12))
    
    def build_pdf_card(item, border_color_hex, category_label="Date"):
        """
        Creates a ReportLab Flowable (Table acting as a card) for a report item.
        """
        stars, label = get_stars_and_label(item["importance_score"])
        topics = ", ".join(item["tags"])
        
        date_str = f"<b>{category_label}:</b> {item['date']}"
        if category_label == "Date" and item.get("event_time"):
            date_str += f" ({item['event_time']})"
            
        author_label = "Authors" if category_label == "Date" else "Speakers/Hosts"
        review_metadata = format_review_metadata(item)
        evidence = item.get("evidence") or []
        
        card_story = [
            Paragraph(item["institution"].upper(), card_institution),
            Paragraph(item["title"], card_title),
            Paragraph(f"{date_str}  |  <b>{author_label}:</b> {item['author']}", card_meta),
            Paragraph(f"<b>Topics:</b> {topics}", card_meta),
            Paragraph(f"<b>Importance:</b> <font color='#c00000'>{stars}</font> ({label})", card_importance),
            Spacer(1, 4),
            Paragraph(f"<b>Why it matters:</b> {item.get('why_it_matters', '')}", card_why),
            Spacer(1, 4),
            Paragraph(item["summary"], card_summary),
            Spacer(1, 4),
            Paragraph(f"<b>Link:</b> <font color='#1f4e78'>{item['url']}</font>", card_link)
        ]
        insert_at = 5
        if review_metadata:
            card_story.insert(insert_at, Paragraph(f"<b>Review:</b> {escape(review_metadata)}", card_meta))
            insert_at += 1
        if evidence:
            evidence_text = escape(format_evidence_compact(evidence))
            card_story.insert(insert_at, Paragraph(f"<b>Evidence checked:</b> {evidence_text}", card_meta))
        
        # We put the card story inside a single cell Table to create the box effect
        card_table = Table([[card_story]], colWidths=[6.4 * inch])
        card_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#fafbfd")),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#e1e4e8")),
            ('LINELEFT', (0,0), (-1,-1), 4, colors.HexColor(border_color_hex)),
            ('TOPPADDING', (0,0), (-1,-1), 12),
            ('BOTTOMPADDING', (0,0), (-1,-1), 12),
            ('LEFTPADDING', (0,0), (-1,-1), 15),
            ('RIGHTPADDING', (0,0), (-1,-1), 15),
        ]))
        return KeepTogether([card_table, Spacer(1, 15)])

    # 1. Publications Section
    story.append(Paragraph("New Publications", section_heading))
    if analyzed_data["reports"]:
        for item in analyzed_data["reports"]:
            story.append(build_pdf_card(item, "#1f4e78", "Date"))
    else:
        story.append(Paragraph("<i>No qualifying items found in this coverage window.</i>", card_summary))
        story.append(Spacer(1, 15))
        
    # 2. Podcasts & Videos Section
    story.append(Paragraph("Podcasts & Videos", section_heading))
    if analyzed_data["podcasts"]:
        for item in analyzed_data["podcasts"]:
            story.append(build_pdf_card(item, "#7030a0", "Date"))
    else:
        story.append(Paragraph("<i>No qualifying items found in this coverage window.</i>", card_summary))
        story.append(Spacer(1, 15))
        
    # 3. Upcoming Events Section
    story.append(Paragraph("Upcoming Events", section_heading))
    if analyzed_data["events"]:
        for item in analyzed_data["events"]:
            story.append(build_pdf_card(item, "#c65911", "Event Date"))
    else:
        story.append(Paragraph("<i>No qualifying items found in this coverage window.</i>", card_summary))
        story.append(Spacer(1, 15))
        
    # Page Break before Sources Checked & Methodology
    story.append(PageBreak())

    # 4. Recall Risks Section
    story.append(Paragraph("Recall Risks", section_heading))
    recall_flags = get_recall_risk_flags(recall_audit)
    recall_details = get_recall_risk_details(recall_audit)
    if recall_flags or recall_details:
        for flag in recall_flags:
            story.append(Paragraph(f"- {escape(str(flag))}", source_checked_style))
        if recall_flags and recall_details:
            story.append(Spacer(1, 8))
        for risk in recall_details[:20]:
            severity = str(risk.get("severity", "unknown")).upper()
            area = risk.get("area", "General")
            issue = risk.get("issue", "")
            status = risk.get("status", "")
            implication = risk.get("implication", "")
            body = f"<b>{escape(severity)} - {escape(str(area))}:</b> {escape(str(issue))}"
            if status:
                body += f"<br/><font color='#666666'>Status: {escape(str(status))}</font>"
            if implication:
                body += f"<br/><font color='#666666'>{escape(str(implication))}</font>"
            story.append(Paragraph(body, source_checked_style))
            story.append(Spacer(1, 5))
    else:
        story.append(Paragraph("<i>No material recall risks flagged by the scanner audit.</i>", card_summary))
    story.append(Spacer(1, 15))
    
    # 5. Sources Checked Section
    story.append(Paragraph("Sources Checked", section_heading))
    sources_data = []
    # Two-column layout for sources checked to save space
    sorted_sources = sorted(status_notes.items())
    half = (len(sorted_sources) + 1) // 2
    col1 = sorted_sources[:half]
    col2 = sorted_sources[half:]
    
    for i in range(half):
        row = []
        inst1, status1 = col1[i]
        row.append(Paragraph(f"- <b>{inst1}</b>:<br/><font color='#666666'><i>{status1}</i></font>", source_checked_style))
        
        if i < len(col2):
            inst2, status2 = col2[i]
            row.append(Paragraph(f"- <b>{inst2}</b>:<br/><font color='#666666'><i>{status2}</i></font>", source_checked_style))
        else:
            row.append(Paragraph("", source_checked_style))
        sources_data.append(row)
        
    sources_table = Table(sources_data, colWidths=[3.2 * inch, 3.2 * inch])
    sources_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(sources_table)
    story.append(Spacer(1, 20))
    
    # 6. Methodology
    story.append(Paragraph("<b>Methodology</b>", ParagraphStyle('MethTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=11, spaceAfter=6, keepWithNext=True)))
    story.append(Paragraph(METHODOLOGY_TEXT, methodology_style))
    
    # Build Document using NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)

def generate_comparison_markdown(results_by_model, run_date_str):
    """
    Generates a Markdown report comparing outputs from multiple models.
    """
    results_by_model = sanitize_report_data(results_by_model)
    models = list(results_by_model.keys())
    
    # URL grouping
    url_to_item = {}
    for model in models:
        for cat in ["reports", "podcasts", "events"]:
            for item in results_by_model[model][cat]:
                url = item.get("url", "").strip()
                if not url:
                    continue
                if url not in url_to_item:
                    url_to_item[url] = {
                        "title": item.get("title"),
                        "institution": item.get("institution"),
                        "category": cat,
                        "url": url,
                        "date": item.get("date"),
                        "author": item.get("author"),
                        "decisions": {}
                    }
                url_to_item[url]["decisions"][model] = item
                
    md = []
    md.append(f"# Economic Security Think Tank Scan - AI Model Comparison")
    md.append(f"**Date:** {run_date_str}")
    md.append(f"**Generated At:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    md.append("## 1. Model Summary Metrics\n")
    md.append("| Model | Total Selected | Publications | Podcasts/Videos | Events | Avg Score |")
    md.append("| --- | --- | --- | --- | --- | --- |")
    for model in models:
        metrics = results_by_model[model]
        r_count = len(metrics["reports"])
        p_count = len(metrics["podcasts"])
        e_count = len(metrics["events"])
        total = r_count + p_count + e_count
        
        scores = []
        for cat in ["reports", "podcasts", "events"]:
            for item in metrics[cat]:
                scores.append(item.get("importance_score", 0))
        avg_score = sum(scores) / len(scores) if scores else 0
        md.append(f"| `{model}` | **{total}** | {r_count} | {p_count} | {e_count} | {avg_score:.2f} |")
    md.append("\n")
    
    md.append("## 2. Selection Comparison Matrix\n")
    headers = ["Institution & Title", "Category"] + [f"`{m.split('/')[-1]}`" for m in models]
    md.append("| " + " | ".join(headers) + " |")
    md.append("| " + " | ".join(["---"] * len(headers)) + " |")
    
    for url, data in url_to_item.items():
        title_disp = f"**{data['institution']}**<br>[{data['title']}]({data['url']})"
        cat_disp = data['category'].capitalize()
        row = [title_disp, cat_disp]
        for model in models:
            if model in data["decisions"]:
                score = data["decisions"][model].get("importance_score", 3)
                row.append(f"Selected (Score {score})")
            else:
                row.append("Filtered Out")
        md.append("| " + " | ".join(row) + " |")
    md.append("\n")
    
    md.append("## 3. Detailed Output Comparison\n")
    item_idx = 1
    for url, data in url_to_item.items():
        md.append(f"### {item_idx}. {data['title']}")
        md.append(f"**Institution:** {data['institution']} | **Category:** {data['category'].capitalize()} | **Link:** [{data['url']}]({data['url']})\n")
        
        for model in models:
            md.append(f"#### Model: `{model}`")
            if model in data["decisions"]:
                item = data["decisions"][model]
                stars, label = get_stars_and_label(item["importance_score"])
                md.append(f"- **Importance:** {stars} ({label})")
                md.append(f"- **Tags:** {', '.join(item.get('tags', []))}")
                md.append(f"- **Why it matters:** *{item.get('why_it_matters', 'N/A')}*")
                md.append(f"- **Summary:** {item.get('summary', 'N/A')}\n")
            else:
                md.append("*Filtered out as irrelevant by this model.*\n")
        md.append("---\n")
        item_idx += 1
        
    return "\n".join(md)

def generate_comparison_html(results_by_model, run_date_str):
    """
    Generates a beautiful HTML report comparing outputs from multiple models.
    """
    results_by_model = sanitize_report_data(results_by_model)
    models = list(results_by_model.keys())
    
    # Group items by URL
    url_to_item = {}
    for model in models:
        for cat in ["reports", "podcasts", "events"]:
            for item in results_by_model[model][cat]:
                url = item.get("url", "").strip()
                if not url:
                    continue
                if url not in url_to_item:
                    url_to_item[url] = {
                        "title": item.get("title"),
                        "institution": item.get("institution"),
                        "category": cat,
                        "url": url,
                        "date": item.get("date"),
                        "author": item.get("author"),
                        "decisions": {}
                    }
                url_to_item[url]["decisions"][model] = item
                
    # Prepare metrics for each model
    model_metrics = {}
    for model in models:
        metrics = results_by_model[model]
        r_count = len(metrics["reports"])
        p_count = len(metrics["podcasts"])
        e_count = len(metrics["events"])
        total = r_count + p_count + e_count
        
        scores = []
        for cat in ["reports", "podcasts", "events"]:
            for item in metrics[cat]:
                scores.append(item.get("importance_score", 0))
        avg_score = sum(scores) / len(scores) if scores else 0
        
        model_metrics[model] = {
            "reports": r_count,
            "podcasts": p_count,
            "events": e_count,
            "total": total,
            "avg_score": avg_score
        }
        
    html_template = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Think Tank Scanner - AI Model Comparison ({{ run_date }})</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --primary: #2b6cb0;
            --primary-dark: #2c5282;
            --success: #38a169;
            --danger: #e53e3e;
            --bg-gray: #f7fafc;
            --text-dark: #2d3748;
            --border-color: #e2e8f0;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-gray);
            color: var(--text-dark);
            margin: 0;
            padding: 0;
            line-height: 1.6;
        }
        .header {
            background: linear-gradient(135deg, #1a202c 0%, #2d3748 100%);
            color: white;
            padding: 40px 20px;
            text-align: center;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .header h1 {
            margin: 0 0 10px 0;
            font-size: 32px;
            letter-spacing: -0.5px;
        }
        .header p {
            margin: 0;
            font-size: 16px;
            color: #a0aec0;
        }
        .container {
            max-width: 1280px;
            margin: 30px auto;
            padding: 0 20px;
        }
        .tabs {
            display: flex;
            border-bottom: 2px solid var(--border-color);
            margin-bottom: 30px;
            gap: 10px;
        }
        .tab-btn {
            background: none;
            border: none;
            padding: 12px 24px;
            font-size: 16px;
            font-weight: 600;
            color: #718096;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            transition: all 0.2s ease;
        }
        .tab-btn:hover {
            color: var(--primary);
        }
        .tab-btn.active {
            color: var(--primary);
            border-bottom-color: var(--primary);
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .card {
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid var(--border-color);
        }
        .card-title {
            font-size: 20px;
            font-weight: 700;
            margin-top: 0;
            margin-bottom: 20px;
            color: #1a202c;
            border-bottom: 2px solid var(--bg-gray);
            padding-bottom: 10px;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .metric-card {
            background: white;
            border-radius: 8px;
            padding: 20px;
            border: 1px solid var(--border-color);
            border-top: 4px solid var(--primary);
            box-shadow: 0 2px 4px rgba(0,0,0,0.02);
            transition: transform 0.2s;
        }
        .metric-card:hover {
            transform: translateY(-2px);
        }
        .metric-model {
            font-family: monospace;
            font-size: 13px;
            color: #718096;
            text-transform: uppercase;
        }
        .metric-value {
            font-size: 36px;
            font-weight: 800;
            color: #1a202c;
            margin: 10px 0;
        }
        .metric-stats {
            font-size: 14px;
            color: #4a5568;
            border-top: 1px solid var(--border-color);
            padding-top: 10px;
            margin-top: 10px;
        }
        .table-responsive {
            overflow-x: auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }
        th, td {
            padding: 16px;
            border-bottom: 1px solid var(--border-color);
            vertical-align: top;
        }
        th {
            background-color: var(--bg-gray);
            color: #4a5568;
            font-weight: 700;
        }
        tr:hover {
            background-color: #fafbfd;
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 600;
        }
        .status-selected {
            background-color: #c6f6d5;
            color: #22543d;
        }
        .status-filtered {
            background-color: #fed7d7;
            color: #742a2a;
        }
        .comp-badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            margin-right: 5px;
        }
        .comp-badge-cat {
            background-color: #ebf8ff;
            color: #2b6cb0;
        }
        .comparison-item {
            border-bottom: 1px solid var(--border-color);
            padding: 24px 0;
        }
        .comparison-item:last-child {
            border-bottom: none;
        }
        .item-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 15px;
        }
        .item-title {
            font-size: 20px;
            font-weight: 700;
            margin: 0;
            color: #1a202c;
        }
        .item-source {
            color: #718096;
            font-size: 14px;
            margin: 5px 0 0 0;
        }
        .comparison-columns {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 20px;
            margin-top: 15px;
        }
        .model-column {
            background-color: var(--bg-gray);
            border-radius: 8px;
            padding: 20px;
            border: 1px solid var(--border-color);
        }
        .model-column.filtered {
            background-color: white;
            border-style: dashed;
            opacity: 0.6;
        }
        .model-column-header {
            font-weight: 700;
            font-size: 15px;
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 8px;
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .model-col-name {
            font-family: monospace;
            color: #2d3748;
        }
        .score-stars {
            color: #d69e2e;
            font-size: 14px;
        }
        .tag-list {
            margin: 10px 0;
        }
        .tag-pill {
            display: inline-block;
            background: #edf2f7;
            color: #4a5568;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            margin-right: 5px;
            margin-bottom: 5px;
        }
        .why-matters-box {
            font-style: italic;
            background-color: #ebf8ff;
            border-left: 3px solid #3182ce;
            padding: 10px;
            border-radius: 0 4px 4px 0;
            margin-bottom: 12px;
            font-size: 14px;
        }
    </style>
</head>
<body>

    <div class="header">
        <h1>AI Model Comparison Dashboard</h1>
        <p>Think Tank Scanner Output Comparison &bull; Date: {{ run_date }}</p>
    </div>

    <div class="container">
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('tab-dashboard')">Overview Metrics</button>
            <button class="tab-btn" onclick="switchTab('tab-matrix')">Selection Matrix</button>
            <button class="tab-btn" onclick="switchTab('tab-details')">Side-by-Side Details</button>
        </div>

        <!-- Dashboard Tab -->
        <div id="tab-dashboard" class="tab-content active">
            <div class="metrics-grid">
                {% for model, metrics in model_metrics.items() %}
                <div class="metric-card">
                    <div class="metric-model">{{ model }}</div>
                    <div class="metric-value">{{ metrics.total }}</div>
                    <div style="font-size: 14px; font-weight: bold; color: #4a5568; margin-bottom: 10px;">Total Matches Selected</div>
                    <div class="metric-stats">
                        <div>Publications: <strong>{{ metrics.reports }}</strong></div>
                        <div>Podcasts/Videos: <strong>{{ metrics.podcasts }}</strong></div>
                        <div>Events: <strong>{{ metrics.events }}</strong></div>
                        <div style="margin-top: 5px; border-top: 1px dashed #e2e8f0; padding-top: 5px;">Avg Importance: <strong>{{ "%.2f" | format(metrics.avg_score) }} / 5.0</strong></div>
                    </div>
                </div>
                {% endfor %}
            </div>
            
            <div class="card">
                <div class="card-title">Comparison Overview</div>
                <p>This report compares the articles selected by different Large Language Models via OpenRouter from the daily scan candidates. Grouping is done by URL, showing how models diverge in relevance detection, classification, and scoring.</p>
            </div>
        </div>

        <!-- Matrix Tab -->
        <div id="tab-matrix" class="tab-content">
            <div class="card">
                <div class="card-title">Selection Comparison Matrix</div>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>Institution & Title</th>
                                <th>Category</th>
                                {% for model in models %}
                                <th>{{ model.split('/')[-1] }}</th>
                                {% endfor %}
                            </tr>
                        </thead>
                        <tbody>
                            {% for url, data in url_to_item.items() %}
                            <tr>
                                <td>
                                    <strong>{{ data.institution }}</strong><br>
                                    <a href="{{ data.url }}" target="_blank" style="color: var(--primary); text-decoration: none;">{{ data.title }}</a>
                                </td>
                                <td>
                                    <span class="comp-badge comp-badge-cat">{{ data.category.capitalize() }}</span>
                                </td>
                                {% for model in models %}
                                <td>
                                    {% if model in data.decisions %}
                                    <span class="status-badge status-selected">Selected ({{ data.decisions[model].importance_score }})</span>
                                    {% else %}
                                    <span class="status-badge status-filtered">Filtered</span>
                                    {% endif %}
                                </td>
                                {% endfor %}
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Details Tab -->
        <div id="tab-details" class="tab-content">
            <div class="card">
                <div class="card-title">Detailed Side-by-Side Outputs</div>
                
                {% for url, data in url_to_item.items() %}
                <div class="comparison-item">
                    <div class="item-header">
                        <div>
                            <h3 class="item-title">{{ data.title }}</h3>
                            <p class="item-source"><strong>{{ data.institution }}</strong> &bull; {{ data.date }} &bull; Author/Speaker: {{ data.author }}</p>
                        </div>
                        <div>
                            <span class="comp-badge comp-badge-cat" style="font-size: 13px; padding: 4px 10px;">{{ data.category.capitalize() }}</span>
                            <a href="{{ data.url }}" target="_blank" style="font-size: 13px; margin-left: 10px; color: var(--primary);">Visit Link &raquo;</a>
                        </div>
                    </div>
                    
                    <div class="comparison-columns">
                        {% for model in models %}
                        {% if model in data.decisions %}
                        {% set dec = data.decisions[model] %}
                        <div class="model-column">
                            <div class="model-column-header">
                                <span class="model-col-name">{{ model.split('/')[-1] }}</span>
                                <span class="score-stars">Score {{ dec.importance_score }}</span>
                            </div>
                            
                            <div class="why-matters-box">
                                <strong>Why it matters:</strong><br>
                                {{ dec.why_it_matters }}
                            </div>
                            
                            <div style="font-size: 14px; margin-bottom: 12px;">
                                <strong>Summary:</strong><br>
                                {{ dec.summary }}
                            </div>
                            
                            <div class="tag-list">
                                {% for tag in dec.tags %}
                                <span class="tag-pill">{{ tag }}</span>
                                {% endfor %}
                            </div>
                        </div>
                        {% else %}
                        <div class="model-column filtered">
                            <div class="model-column-header" style="opacity: 0.5;">
                                <span class="model-col-name">{{ model.split('/')[-1] }}</span>
                                <span style="font-size: 12px; color: var(--danger);">FILTERED OUT</span>
                            </div>
                            <p style="font-size: 13px; color: #718096; text-align: center; margin: 20px 0;">This model determined the item was not relevant to economic security.</p>
                        </div>
                        {% endif %}
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
                
            </div>
        </div>
    </div>

    <script>
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            
            document.getElementById(tabId).classList.add('active');
            
            // Find button that has matching switchTab call
            const btns = document.querySelectorAll('.tab-btn');
            btns.forEach(btn => {
                if (btn.getAttribute('onclick').includes(tabId)) {
                    btn.classList.add('active');
                }
            });
        }
    </script>
</body>
</html>
"""
    
    t = Template(html_template)
    html_output = t.render(
        run_date=run_date_str,
        models=models,
        url_to_item=url_to_item,
        model_metrics=model_metrics
    )
    return html_output
