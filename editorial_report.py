"""Shared editorial content for static email HTML and paginated PDF reports."""
import html
import os
from datetime import datetime
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, KeepTogether

import scan_runtime


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
            item.update(summary=summary, why_it_matters=why, url=safe_url(item.get("url")), category_label=label)
            detail = item.get("publication_date_source_detail") or item.get("date_source", "")
            item["publication_evidence_label"] = ("Publisher publication metadata" if "metadata" in detail or "citation" in detail or detail == "page_publication"
                else "Publication date displayed by publisher" if "visible" in detail else "Publisher feed publication date" if "rss" in detail or "atom" in detail else "See publication audit")
            items.append(item)
    prior = int(meta.get("prior_delivered_today", 0))
    source_rows = audit.get("source_health", {}).get("sources", [])
    degraded = [r["source"] for r in source_rows if scan_runtime.source_state(r.get("status", "")) in {"degraded", "unavailable"}]
    unknown = audit.get("enriched_candidate_summary", {}).get("date_status", {}).get("date_unknown", 0)
    selection = audit.get("review_selection", {})
    reasons = selection.get("reason_counts", {})
    pending = meta.get("pending_status_counts", {})
    analysis_metrics = data.get("analysis_metrics", {})
    cutoff = meta.get("coverage_end", "")
    if cutoff:
        cutoff = datetime.fromisoformat(cutoff).strftime("%d %b %Y, %H:%M %Z")
    headline = "Additional reading since the earlier edition" if prior else "Economic security, in focus"
    if not items:
        headline = "No additional verified items" if prior else "No verified items for this edition"
    return {"date": datetime.strptime(date, "%Y-%m-%d").strftime("%A, %d %B %Y"),
            "headline": headline, "edition": "Supplement" if prior else "Daily edition", "prior": prior,
            "items": items, "count": len(items), "cutoff": cutoff, "run_id": meta.get("run_id", ""),
            "unknown": unknown, "degraded": degraded, "coverage_limited": bool(unknown or degraded),
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
    ink = colors.HexColor("#162938")
    teal = colors.HexColor("#185e65")
    muted = colors.HexColor("#52616b")
    line = colors.HexColor("#d8dcd9")
    styles = {
        "brand": ParagraphStyle("brand", fontName="Helvetica", fontSize=9, leading=13, textColor=teal, spaceAfter=14),
        "hero": ParagraphStyle("hero", fontName="Times-Roman", fontSize=30, leading=34, textColor=ink, spaceAfter=14),
        "title": ParagraphStyle("title", fontName="Times-Roman", fontSize=21, leading=25, textColor=ink, spaceAfter=11),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=15, textColor=ink, spaceAfter=12),
        "why": ParagraphStyle("why", fontName="Helvetica", fontSize=11, leading=16, textColor=ink, spaceAfter=14),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=12, textColor=muted, spaceAfter=9),
        "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8, leading=12, textColor=teal, spaceAfter=5),
    }
    def p(text, style="body"):
        return Paragraph(html.escape(str(text or "")).replace("\n", "<br/>"), styles[style])
    story = [p("ECONOMIC SECURITY  /  THINK TANK BRIEF", "brand"),
             p(c["date"] + "  |  " + c["edition"], "small"), p(c["headline"], "hero")]
    if c["prior"]:
        story.append(p(f'{c["prior"]} items were delivered earlier today. This supplement contains {c["count"]} additional {"item" if c["count"] == 1 else "items"}.'))
    story.extend([p(f'{c["count"]} included   |   {c["repeats"]} previously reported candidates removed   |   {c["reviewed"]} sent for model review', "small"), HRFlowable(width="100%", color=line), Spacer(1, 14)])
    if c["coverage_limited"]:
        story.append(p("COVERAGE INCOMPLETE", "label"))
        story.append(p(f'{c["unknown"]} candidates have unverified publication dates. ' + ("Discovery issues: " + "; ".join(c["degraded"]) if c["degraded"] else ""), "small"))
    for index, item in enumerate(c["items"], 1):
        story.extend([Spacer(1, 12), KeepTogether([p(f'{index:02d}  {item["category_label"].upper()}  /  {item.get("institution", "")}', "label"), p(item.get("title"), "title")]),
                      p(f'{item.get("author", "")}  |  Published {item.get("date", "Date unverified")}', "small"),
                      p("WHY IT MATTERS", "label"), p(item.get("why_it_matters"), "why"),
                      p(item.get("summary"))])
        if item.get("event_start_at"):
            story.append(p("Event starts: " + item["event_start_at"], "small"))
        story.append(p(f'Priority {item.get("importance_score", "—")} / 5  |  Relevance: {item.get("relevance_confidence", "unrated")}  |  ' + "; ".join(item.get("tags", [])), "small"))
        if item["url"]:
            story.append(Paragraph('<link href="' + html.escape(item["url"], quote=True) + '" color="#185e65">Read the original publication</link>', styles["body"]))
        story.extend([p("Publication evidence: " + str(item.get("publication_evidence_label")) + " | First discovered: " + str(item.get("first_seen_run_date", "Unknown")), "small"), HRFlowable(width="100%", color=line)])
    story.extend([Spacer(1, 20), p("SCAN QUALITY", "label"),
                  p(f'{c["discovered"]} candidates assessed; {c["skipped"]} filtered before review; {c["selected"]} selected. {c["cached"]} cached decisions reused; {c["reviewed"]} sent to the model. {c["excluded"]} excluded after review; {c["needs_review"]} need analyst review.', "small"),
                  p(f'Persistent verification queue: {c["pending"]} pending; {c["paused"]} paused for attention.', "small"),
                  p("Coverage ends: " + (c["cutoff"] or "See run audit") + ". Sources recovering from a gap may have longer lookback windows.", "small")])
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    doc = SimpleDocTemplate(output_path, pagesize=A4, rightMargin=45, leftMargin=45, topMargin=40, bottomMargin=45,
                           title="Economic Security Think Tank Brief", author="Think Tank Scanner")
    def page_frame(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(teal)
        canvas.setLineWidth(4)
        canvas.line(45, A4[1] - 22, A4[0] - 45, A4[1] - 22)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(muted)
        canvas.drawString(45, 24, c["edition"] + " | " + c["run_id"])
        canvas.drawRightString(A4[0] - 45, 24, str(document.page))
        canvas.restoreState()
    doc.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
