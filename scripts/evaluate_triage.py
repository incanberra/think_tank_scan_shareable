"""Replay frozen candidates without email or production delivery-history writes.

Examples are model comparisons, not reader-labelled accuracy measurements.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyzer
import config
import content_extractor
import editorial_policy
import editorial_report
import scan_runtime
import triage


def read_candidates(root, count, single_date):
    groups = defaultdict(list)
    used = set()
    manifests = sorted((root / "runs").glob("*/run.json"), reverse=True)
    for manifest in manifests[:30]:
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        date = meta.get("run_date")
        if not date or single_date and date != single_date:
            continue
        files = list((manifest.parent / "audit").glob("enriched_candidates_*.jsonl"))
        decision_files = list((manifest.parent / "audit").glob("analysis_decisions_*glm*.json"))
        if not files or not decision_files:
            continue
        decisions = json.loads(decision_files[0].read_text(encoding="utf-8"))
        outcomes = {r["url"]: key for key in ("included", "excluded", "needs_review") for r in decisions.get(key, [])}
        for line in files[0].read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            url = item.get("canonical_url") or item.get("url")
            if url in used or not item.get("review_selected"):
                continue
            used.add(url)
            item["baseline_outcome"] = outcomes.get(url, "unresolved")
            item["snapshot_date"] = date
            groups[item["baseline_outcome"]].append(item)
        if single_date or sum(map(len, groups.values())) >= count * 2:
            break
    items = []
    # Stratify across original included/excluded/held outcomes, not relevance labels.
    while any(groups.values()) and len(items) < count:
        for group in groups.values():
            if group and len(items) < count:
                items.append(group.pop(0))
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--mode", choices=("off", "shadow", "active"), default="shadow")
    parser.add_argument("--analyse", action="store_true", help="Also call GLM and render an unsent draft")
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--reasoning-effort", choices=("none", "minimal", "low", "medium", "high"))
    parser.add_argument("--lowy-html", type=Path, help="Optional frozen public HTML for the demonstrated Lowy case")
    args = parser.parse_args()
    # Keep all mutable state inside the branch's ignored reports directory.
    sandbox = Path(__file__).resolve().parents[1] / "reports"
    output = args.output.resolve()
    if not output.is_relative_to(sandbox.resolve()):
        parser.error("Output must be within the test checkout's reports directory")
    output.mkdir(parents=True, exist_ok=True)
    config.SEEN_LEDGER_PATH = str(output / "state" / "seen_items.json")
    config.ENRICHMENT_CACHE_DIR = str(output / "cache")
    config.TRIAGE_MODE = args.mode
    if args.reasoning_effort:
        config.REVIEW_REASONING_EFFORT = args.reasoning_effort
    items = read_candidates(args.snapshots.resolve(), max(1, min(250, args.limit)), args.date)
    if args.lowy_html:
        body = content_extractor.extract_text_from_html(args.lowy_html.read_text(encoding="utf-8"))
        for item in items:
            if "speed-over-judgement-is-winning-the-ai-arms-race" in item.get("url", ""):
                item.update(extracted_text=body, extracted_text_chars=len(body), evidence_quality="sufficient")
    for item in items:
        item["evidence_quality"] = editorial_policy.body_quality(item)
    (output / "frozen_candidates.json").write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    run = scan_runtime.ScanRun(output, args.date or "2026-09-30", historical=True)
    started = time.monotonic()
    with scan_runtime.activate(run):
        if args.analyse:
            result = analyzer.analyze_items(items, args.model)
            run.record_analysis(result)
            audit = {"evaluation": True, "review_selection": {"total_enriched_candidates": len(items),
                     "selected_for_review": len(items), "skipped_before_review": 0},
                     "enriched_candidate_summary": {"date_status": dict(Counter(i.get("date_status", "unknown") for i in items))}}
            (output / "draft.html").write_text(editorial_report.generate_html(result, run.run_date, {}, recall_audit=audit), encoding="utf-8")
            editorial_report.generate_pdf(result, run.run_date, {}, str(output / "draft.pdf"), recall_audit=audit)
            (output / "analysis.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            records = result["triage"]
        else:
            eligible = [i for i in items if i["evidence_quality"] == "sufficient"]
            records = triage.evaluate(eligible, run)
        by_url = {i.get("canonical_url") or i.get("url"): i for i in items}
        for record in records:
            record["baseline_outcome"] = by_url[record["url"]]["baseline_outcome"]
        requests = getattr(run, "model_requests", [])
        summary = {"sample_size": len(items), "jev_decisions": len(records),
                   "thin_evidence_held_before_jev": sum(i["evidence_quality"] != "sufficient" for i in items),
                   "routes": dict(Counter(r["proposed_route"] for r in records)),
                   "elapsed_seconds": round(time.monotonic() - started, 3),
                   "requests_including_retries": len(requests),
                   "reported_cost_usd": sum((r.get("usage") or {}).get("cost", 0) or 0 for r in requests),
                   "served_models": sorted({r["actual_model"] for r in requests if r.get("actual_model")}),
                   "reasoning_effort": config.REVIEW_REASONING_EFFORT or "provider_default",
                   "reader_labelled_accuracy": None, "email_sent": False}
        (output / "triage.json").write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        run.save_manifest("evaluation_complete", email_sent=False)
        print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
