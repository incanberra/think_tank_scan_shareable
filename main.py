import argparse
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import eligibility
import scan_runtime
from state_storage import locked

import analyzer
import audit_logger
import candidate_selector
import config
import content_extractor
import ddg_searcher
import emailer
import report_generator
import rss_parser
import seen_ledger
import source_discovery
import topic_utils


def slugify_model_name(name):
    """
    Converts a model ID (e.g. google/gemini-2.5-flash) into a safe file slug.
    """
    slug = name.replace("/", "-").replace(":", "-")
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-").lower()


def print_stage(number, label):
    print(f"\n=== Stage {number}: {label} ===")


def prompt_for_model():
    """
    Prompts the user interactively in the console to choose a model or run comparison.
    """
    print("\n=== OpenRouter Model Selection ===")
    print("Select an OpenRouter model to run the daily think tank scan:")

    recommended = config.OPENROUTER_RECOMMENDED_MODELS
    for idx, (model_id, label) in enumerate(recommended, 1):
        print(f"  {idx}) {label} ({model_id})")

    print(f"  {len(recommended) + 1}) Compare All Recommended Models")
    print(f"  {len(recommended) + 2}) Enter a custom OpenRouter model ID")

    default_model = config.OPENROUTER_MODEL
    print(f"\nPress Enter to use the default model: {default_model}")

    try:
        choice = input(f"Select option [1-{len(recommended) + 2}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n[*] Selection cancelled. Defaulting to configured model.")
        return [default_model]

    if not choice:
        print(f"[*] Using default model: {default_model}")
        return [default_model]

    try:
        choice_idx = int(choice)
        if 1 <= choice_idx <= len(recommended):
            selected_model = recommended[choice_idx - 1][0]
            print(f"[*] Selected model: {selected_model}")
            return [selected_model]
        if choice_idx == len(recommended) + 1:
            print("[*] Selected comparison mode (all recommended models).")
            return [m[0] for m in recommended]
        if choice_idx == len(recommended) + 2:
            custom_id = input(
                "Enter custom OpenRouter model ID (e.g. meta-llama/llama-3.1-405b-instruct): "
            ).strip()
            if not custom_id:
                print(f"[!] Invalid input. Using default model: {default_model}")
                return [default_model]
            print(f"[*] Selected custom model: {custom_id}")
            return [custom_id]
        print(f"[!] Invalid option. Using default model: {default_model}")
        return [default_model]
    except ValueError:
        print(f"[!] Invalid input. Using default model: {default_model}")
        return [default_model]


def parse_args():
    parser = argparse.ArgumentParser(description="Economic Security Think Tank Scan")
    parser.add_argument(
        "--date",
        type=str,
        help="Run date in YYYY-MM-DD format (defaults to current date in Canberra time)",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Skip sending the report via email",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="reports",
        help="Directory to save generated reports (default: reports/)",
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        help="OpenRouter model name(s) (comma-separated for comparison, overrides .env)",
    )
    parser.add_argument(
        "--compare",
        "-c",
        action="store_true",
        help="Compare all recommended models",
    )
    parser.add_argument(
        "--skip-enrichment",
        action="store_true",
        help="Skip full-page fetch/extraction and review only discovered metadata",
    )
    parser.add_argument(
        "--backfill-seen-ledger",
        action="store_true",
        help="Backfill the persistent first-seen ledger from historical enriched candidate audits, then exit",
    )
    parser.add_argument("--as-of", help="Coverage end: now, or an ISO-8601 timestamp with timezone")
    parser.add_argument("--reprocess", action="store_true", help="Bypass relevance-decision cache; previously reported items stay excluded")
    parser.add_argument("--retry-pending", action="store_true", help="Retry pending and paused verification cases now")
    return parser.parse_args()


def resolve_run_date(args):
    canberra_tz = ZoneInfo(config.TIMEZONE_CANBERRA)
    now_canberra = datetime.now(canberra_tz)

    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
            return args.date
        except ValueError:
            print(f"[!] Error: Invalid date format '{args.date}'. Must be YYYY-MM-DD.")
            sys.exit(1)

    return now_canberra.strftime("%Y-%m-%d")


def stage_discover(run_date_str):
    print_stage(1, "Discover candidates")
    rss_items, rss_status = rss_parser.fetch_rss_items(run_date_str, config.COVERAGE_WINDOW_HOURS)
    native_items, native_status, native_audit = source_discovery.discover_search_only_sources(
        run_date_str,
        config.COVERAGE_WINDOW_HOURS,
    )

    rss_failed_sources = [
        source
        for source, status in rss_status.items()
        if "failed" in str(status).lower()
    ]
    native_fallback_items = []
    native_fallback_status = {}
    native_fallback_audit = {}
    if rss_failed_sources:
        print(f"[*] Running source-native fallback for {len(rss_failed_sources)} RSS-failed sources.")
        native_fallback_items, native_fallback_status, native_fallback_audit = source_discovery.discover_native_sources(
            run_date_str,
            config.COVERAGE_WINDOW_HOURS,
            source_names=rss_failed_sources,
        )

    search_items = []
    search_status = {}
    if config.ENABLE_DDG_FALLBACK:
        native_sources_with_items = {item.get("institution") for item in native_items}
        ddg_sources = [
            source
            for source in config.SEARCH_ONLY_THINK_TANKS
            if source not in native_sources_with_items
        ]
        if ddg_sources:
            print(f"[*] DDG fallback enabled for {len(ddg_sources)} sources without native candidates.")
            search_items, search_status = ddg_searcher.search_think_tanks_via_ddg(
                run_date_str,
                config.COVERAGE_WINDOW_HOURS,
                source_names=ddg_sources,
            )
        else:
            print("[*] DDG fallback enabled but all search-only sources had native candidates.")
    else:
        print("[*] DDG fallback disabled. Set ENABLE_DDG_FALLBACK=true to enable unpaid DDG fallback.")

    raw_candidates = rss_items + native_items + native_fallback_items + search_items
    status_notes = {**rss_status, **native_status}
    for source, status in native_fallback_status.items():
        rss_note = status_notes.get(source)
        status_notes[source] = f"{rss_note}; native fallback {status}" if rss_note else status
    for source, status in search_status.items():
        native_note = status_notes.get(source)
        status_notes[source] = f"{native_note}; DDG {status}" if native_note else status

    run = scan_runtime.current()
    if run:
        raw_candidates = run.prepare_candidates(raw_candidates)
        run.status_notes = status_notes
    print(f"[*] Total raw candidates discovered from all sources: {len(raw_candidates)}")
    discovery_audit = {"native_discovery": merge_native_discovery_audits(native_audit, native_fallback_audit)}
    return raw_candidates, status_notes, discovery_audit


def merge_native_discovery_audits(*audits):
    merged_counts = {}
    samples = []
    for audit in audits:
        if not audit:
            continue
        for reason, count in (audit.get("native_rejection_counts") or {}).items():
            merged_counts[reason] = merged_counts.get(reason, 0) + count
        samples.extend(audit.get("native_rejected_samples") or [])
    return {
        "native_rejection_counts": dict(sorted(merged_counts.items())),
        "native_rejected_samples": samples[:50],
        "native_rejected_total": sum(merged_counts.values()),
    }


def stage_enrich_select_and_audit(raw_candidates, status_notes, discovery_audit, output_dir, run_date_str, skip_enrichment):
    print_stage(2, "Enrich, select, and audit candidates")
    print("[*] Enriching candidates with page text, metadata, and date checks...")
    enriched_candidates, enrichment_audit = content_extractor.enrich_items(
        raw_candidates,
        run_date_str,
        config.COVERAGE_WINDOW_HOURS,
        fetch_pages=not skip_enrichment,
    )
    enrichment_audit["discovery"] = discovery_audit
    all_candidates = topic_utils.annotate_topic_hints(enriched_candidates)
    seen_audit = seen_ledger.annotate_items_with_seen_metadata(all_candidates, output_dir, run_date_str)
    enrichment_audit["seen_ledger"] = seen_audit
    print(
        f"[*] Seen ledger: {seen_audit['new_this_run']} new candidates, "
        f"{seen_audit['seen_before']} seen before ({seen_audit['seen_ledger_path']})."
    )
    review_candidates, review_selection_audit = candidate_selector.select_candidates_for_review(all_candidates)
    enrichment_audit["review_selection"] = review_selection_audit
    if scan_runtime.current():
        scan_runtime.current().record_selection(all_candidates)

    print(
        f"[*] Selected {len(review_candidates)} of {len(all_candidates)} enriched candidates for LLM review "
        f"({review_selection_audit['skipped_before_review']} skipped before review)."
    )

    audit_paths = audit_logger.save_run_audit(
        output_dir,
        run_date_str,
        raw_candidates,
        all_candidates,
        status_notes,
        enrichment_audit,
    )
    print(f"[+] Saved raw candidate audit: {audit_paths['raw_candidates_path']}")
    print(f"[+] Saved enriched candidate audit: {audit_paths['enriched_candidates_path']}")
    print(f"[+] Saved recall audit: {audit_paths['recall_audit_path']}")
    print(f"[+] Saved source health audit: {audit_paths['source_health_path']}")
    return all_candidates, review_candidates, enrichment_audit, audit_paths


def resolve_models(args):
    print_stage(3, "Resolve model selection")
    if args.compare:
        print("[*] CLI Compare flag active. Running comparison for all recommended models.")
        return [m[0] for m in config.OPENROUTER_RECOMMENDED_MODELS]

    if args.model:
        models_to_run = [m.strip() for m in args.model.split(",") if m.strip()]
        print(f"[*] CLI Model argument active: {models_to_run}")
        return models_to_run

    if sys.stdin.isatty():
        return prompt_for_model()

    print(f"[*] Non-interactive run. Using default model from config: {config.OPENROUTER_MODEL}")
    return [config.OPENROUTER_MODEL]


def analysis_counts(analyzed_data):
    return {
        "reports": len(analyzed_data.get("reports", [])),
        "podcasts": len(analyzed_data.get("podcasts", [])),
        "events": len(analyzed_data.get("events", [])),
        "excluded": len(analyzed_data.get("excluded", [])),
        "needs_review": len(analyzed_data.get("needs_review", [])),
    }


def map_item_for_json(item):
    obj = {
        "title": item["title"],
        "institution": item["institution"],
        "date": item["date"],
        "author": item["author"],
        "tags": item["tags"],
        "summary": f"Why it matters:\n{item.get('why_it_matters', '')}\n\nSummary:\n{item['summary']}",
        "importance_score": item["importance_score"],
        "url": item["url"],
    }
    optional_fields = [
        "publication_date_source_detail",
        "evidence_quality",
        "decision_cache_status",
        "scan_run_id",
        "date_source",
        "published_at_verified",
        "modified_at",
        "modified_date_source",
        "event_start_at",
        "event_end_at",
        "relevance_confidence",
        "evidence",
        "date_status",
        "date_note",
        "extraction_status",
        "extracted_text_chars",
        "discovery_methods",
        "content_format",
        "cache_status",
        "seen_item_key",
        "seen_status",
        "first_seen_run_date",
        "last_seen_run_date",
        "first_verified_publication_date",
        "last_verified_publication_date",
        "date_crosscheck_status",
        "date_crosscheck_note",
        "content_hash",
        "content_changed_since_last_seen",
        "last_reported_run_date",
        "last_emailed_run_date",
    ]
    for field in optional_fields:
        if item.get(field):
            obj[field] = item[field]
    if item.get("event_time"):
        obj["event_time"] = item["event_time"]
    return obj


def build_clean_json_data(
    analyzed_data,
    run_date_str,
    audit_paths,
    analysis_audit_path,
    source_health_paths,
):
    counts = analysis_counts(analyzed_data)
    recall_audit = audit_paths["recall_audit"]
    return {
        "run_date": run_date_str,
        "run": scan_runtime.current().metadata() if scan_runtime.current() else {},
        "analysis_metrics": analyzed_data.get("analysis_metrics", {}),
        "coverage_window": f"{config.COVERAGE_WINDOW_HOURS} hours",
        "reports": [map_item_for_json(item) for item in analyzed_data["reports"]],
        "events": [map_item_for_json(item) for item in analyzed_data["events"]],
        "podcasts": [map_item_for_json(item) for item in analyzed_data["podcasts"]],
        "audit": {
            "raw_candidates_file": audit_paths["raw_candidates_path"],
            "enriched_candidates_file": audit_paths["enriched_candidates_path"],
            "recall_audit_file": audit_paths["recall_audit_path"],
            "analysis_decisions_file": analysis_audit_path,
            "source_health_file": source_health_paths["source_health_path"],
            "source_health_history_file": source_health_paths["source_health_history_path"],
            "seen_ledger_file": audit_paths.get("seen_ledger_path"),
            "excluded_count": counts["excluded"],
            "needs_review_count": counts["needs_review"],
            "recall_risk_flags": recall_audit.get("recall_risk_flags", []),
            "recall_risk_details": recall_audit.get("recall_risk_details", []),
        },
    }


def write_text(path, content):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def render_model_outputs(
    output_dir,
    run_date_str,
    model_slug,
    analyzed_data,
    status_notes,
    audit_paths,
    analysis_audit_path,
    source_health_paths,
    write_standard_copies=False,
):
    eligibility.filter_for_publication(analyzed_data, output_dir, run_date_str)
    clean_json_data = build_clean_json_data(
        analyzed_data,
        run_date_str,
        audit_paths,
        analysis_audit_path,
        source_health_paths,
    )

    json_path = os.path.join(output_dir, f"report_{run_date_str}_{model_slug}.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(clean_json_data, handle, indent=2, ensure_ascii=False)
    print(f"[+] Saved JSON for {model_slug}: {json_path}")

    markdown_data = {
        "reports": [dict(item) for item in analyzed_data["reports"]],
        "podcasts": [dict(item) for item in analyzed_data["podcasts"]],
        "events": [dict(item) for item in analyzed_data["events"]],
        "excluded": [dict(item) for item in analyzed_data.get("excluded", [])],
        "needs_review": [dict(item) for item in analyzed_data.get("needs_review", [])],
    }
    markdown_report = report_generator.generate_markdown(
        markdown_data,
        run_date_str,
        status_notes,
        recall_audit=audit_paths["recall_audit"],
    )
    md_path = os.path.join(output_dir, f"report_{run_date_str}_{model_slug}.md")
    write_text(md_path, markdown_report)
    print(f"[+] Saved Markdown report for {model_slug}: {md_path}")

    html_report = report_generator.generate_html(
        analyzed_data,
        run_date_str,
        status_notes,
        raw_json_str="",
        recall_audit=audit_paths["recall_audit"],
    )
    html_path = os.path.join(output_dir, f"report_{run_date_str}_{model_slug}.html")
    write_text(html_path, html_report)
    print(f"[+] Saved HTML report for {model_slug}: {html_path}")

    pdf_path = os.path.join(output_dir, f"report_{run_date_str}_{model_slug}.pdf")
    report_generator.generate_pdf(
        analyzed_data,
        run_date_str,
        status_notes,
        pdf_path,
        recall_audit=audit_paths["recall_audit"],
    )
    print(f"[+] Saved PDF report for {model_slug}: {pdf_path}")

    if write_standard_copies:
        std_json_path = os.path.join(output_dir, f"report_{run_date_str}.json")
        std_md_path = os.path.join(output_dir, f"report_{run_date_str}.md")
        std_html_path = os.path.join(output_dir, f"report_{run_date_str}.html")
        std_pdf_path = os.path.join(output_dir, f"report_{run_date_str}.pdf")
        with open(std_json_path, "w", encoding="utf-8") as handle:
            json.dump(clean_json_data, handle, indent=2, ensure_ascii=False)
        write_text(std_md_path, markdown_report)
        write_text(std_html_path, html_report)
        report_generator.generate_pdf(
            analyzed_data,
            run_date_str,
            status_notes,
            std_pdf_path,
            recall_audit=audit_paths["recall_audit"],
        )
        print("[+] Saved standard reports (copies of primary model).")

    return {
        "json_path": json_path,
        "md_path": md_path,
        "html_path": html_path,
        "pdf_path": pdf_path,
        "html": html_report,
        "clean_json_data": clean_json_data,
    }


def stage_analyze_and_render(
    models_to_run,
    review_candidates,
    raw_candidates,
    all_candidates,
    status_notes,
    enrichment_audit,
    audit_paths,
    output_dir,
    run_date_str,
):
    results_by_model = {}
    rendered_by_model = {}

    for idx, model in enumerate(models_to_run):
        print_stage(4, f"Analyze candidates with {model}")
        model_slug = slugify_model_name(model)
        analyzed_data = analyzer.analyze_items(review_candidates, override_model=model)
        analyzed_data = eligibility.filter_for_publication(analyzed_data, output_dir, run_date_str)
        if scan_runtime.current():
            scan_runtime.current().record_analysis(analyzed_data)
        results_by_model[model] = analyzed_data
        analysis_audit_path = audit_logger.save_analysis_audit(
            output_dir,
            run_date_str,
            model_slug,
            analyzed_data,
        )
        source_health_paths = audit_logger.save_source_health_audit(
            output_dir,
            run_date_str,
            model_slug,
            raw_candidates,
            all_candidates,
            status_notes,
            enrichment_audit,
            analyzed_data=analyzed_data,
        )

        counts = analysis_counts(analyzed_data)
        print(
            f"[+] Screened and verified {counts['reports']} publications, "
            f"{counts['podcasts']} podcasts/videos, and {counts['events']} events. "
            f"Excluded {counts['excluded']}; queued {counts['needs_review']} for review."
        )
        print(f"[+] Saved analysis decision audit: {analysis_audit_path}")
        print(f"[+] Saved source health audit: {source_health_paths['source_health_path']}")

        print_stage(5, f"Render report files for {model}")
        rendered_by_model[model] = render_model_outputs(
            output_dir,
            run_date_str,
            model_slug,
            analyzed_data,
            status_notes,
            audit_paths,
            analysis_audit_path,
            source_health_paths,
            write_standard_copies=(idx == 0),
        )
        reported_audit = seen_ledger.mark_reported_items(output_dir, run_date_str, model_slug, analyzed_data)
        print(
            f"[+] Updated seen ledger with {reported_audit['reported_count']} rendered report items: "
            f"{reported_audit['seen_ledger_path']}"
        )

    return results_by_model, rendered_by_model


def stage_generate_comparison(results_by_model, output_dir, run_date_str):
    if len(results_by_model) <= 1:
        return ""

    print_stage(6, "Render model comparison")
    comp_md = report_generator.generate_comparison_markdown(results_by_model, run_date_str)
    comp_md_path = os.path.join(output_dir, f"report_{run_date_str}_comparison.md")
    write_text(comp_md_path, comp_md)
    print(f"[+] Saved Markdown Comparison report: {comp_md_path}")

    comp_html = report_generator.generate_comparison_html(results_by_model, run_date_str)
    comp_html_path = os.path.join(output_dir, f"report_{run_date_str}_comparison.html")
    write_text(comp_html_path, comp_html)
    print(f"[+] Saved HTML Comparison report: {comp_html_path}")
    return comp_html


def stage_email(args, run_date_str, models_to_run, results_by_model, rendered_by_model, comparison_html):
    print_stage(7, "Email report")
    if args.no_email:
        print("[*] Skipping email dispatch (--no-email active).")
        return False

    primary_model = models_to_run[0]
    primary_rendered = rendered_by_model[primary_model]
    primary_counts = analysis_counts(results_by_model[primary_model])

    if len(models_to_run) > 1:
        print("[*] Emailing comparison report...")
        email_sent = emailer.send_report_email(
            run_date_str,
            comparison_html,
            primary_rendered["pdf_path"],
            model_name=primary_model,
            summary_counts=primary_counts,
            is_comparison=True,
        )
    else:
        print("[*] Emailing primary model report...")
        email_sent = emailer.send_report_email(
            run_date_str,
            primary_rendered["html"],
            primary_rendered["pdf_path"],
            model_name=primary_model,
            summary_counts=primary_counts,
        )

    if email_sent:
        print("[+] Email successfully dispatched!")
        for model in models_to_run:
            model_slug = slugify_model_name(model)
            delivery_audit = seen_ledger.mark_delivery_items(
                args.output_dir,
                run_date_str,
                model_slug,
                results_by_model[model],
                delivery_status="emailed",
            )
            print(
                f"[+] Updated seen ledger delivery history for {delivery_audit['delivered_count']} items: "
                f"{delivery_audit['seen_ledger_path']}"
            )
    else:
        print("[!] Email dispatch failed. See console warnings.")
    return email_sent


def print_summary(run_date_str, models_to_run, results_by_model, audit_paths, output_dir, email_sent):
    print("\n=== Scan Executive Summary ===")
    print(f"Date: {run_date_str}")
    print(f"Models Run: {models_to_run}")
    for model in models_to_run:
        counts = analysis_counts(results_by_model[model])
        print(
            f"  - {model}: {counts['reports']} reports, {counts['podcasts']} podcasts, "
            f"{counts['events']} events, {counts['excluded']} excluded, "
            f"{counts['needs_review']} review"
        )
    print(f"Recall audit: {audit_paths['recall_audit_path']}")
    print(f"Source health history: {audit_paths['source_health_history_path']}")
    print(f"Email sent: {email_sent}")
    print(f"Output files stored in: {os.path.abspath(output_dir)}")
    print("==============================\n")


@locked
def main():
    args = parse_args()
    run_date_str = resolve_run_date(args)
    if args.backfill_seen_ledger:
        os.makedirs(args.output_dir, exist_ok=True)
        print(json.dumps(seen_ledger.backfill_seen_ledger_from_audits(args.output_dir), indent=2))
        return
    cutoff = None
    if args.as_of:
        if args.as_of == "now":
            cutoff = datetime.now(ZoneInfo(config.TIMEZONE_CANBERRA))
        else:
            cutoff = datetime.fromisoformat(args.as_of)
            if cutoff.tzinfo is None:
                raise ValueError("--as-of must include a timezone, or use now")
            cutoff = cutoff.astimezone(ZoneInfo(config.TIMEZONE_CANBERRA))
        run_date_str = cutoff.strftime("%Y-%m-%d")
    run = scan_runtime.ScanRun(args.output_dir, run_date_str, cutoff=cutoff,
                               reprocess=args.reprocess, retry_pending=args.retry_pending,
                               historical=bool(args.date))
    args.output_dir = str(run.output_dir)
    with scan_runtime.activate(run):
        print(f"[*] Starting scan {run.run_id} for {run_date_str}")
        print(f"[*] Coverage ends {run.cutoff.isoformat()}; output: {run.output_dir}")
        with run.stage("discovery"):
            raw_candidates, status_notes, discovery_audit = stage_discover(run_date_str)
        with run.stage("enrichment_and_selection"):
            all_candidates, review_candidates, enrichment_audit, audit_paths = stage_enrich_select_and_audit(
                raw_candidates, status_notes, discovery_audit, args.output_dir, run_date_str, args.skip_enrichment)
        models_to_run = resolve_models(args)
        if not models_to_run:
            raise ValueError("No models selected")
        run.models = models_to_run
        with run.stage("analysis_and_rendering"):
            results_by_model, rendered_by_model = stage_analyze_and_render(
                models_to_run, review_candidates, raw_candidates, all_candidates, status_notes,
                enrichment_audit, audit_paths, args.output_dir, run_date_str)
            comparison_html = stage_generate_comparison(results_by_model, args.output_dir, run_date_str)
        with run.stage("email"):
            email_sent = stage_email(args, run_date_str, models_to_run, results_by_model, rendered_by_model, comparison_html)
        if not args.no_email and not email_sent:
            raise RuntimeError("Reports were saved, but email delivery failed")
        run.finish(email_sent)
        print_summary(run_date_str, models_to_run, results_by_model, audit_paths, args.output_dir, email_sent)


if __name__ == "__main__":
    main()
