"""Replay included articles for topic labels only; never email or update live state."""
import argparse
from collections import Counter
import copy
import csv
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import editorial_report
import scan_runtime
import topic_classifier
import topic_review
import article_scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Directory containing analysis.json and frozen_candidates.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date", default="2026-09-30")
    parser.add_argument("--confirm", action="store_true", help="Audit pilot labels with fresh, quote-grounded GLM checks")
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--prefer-providers", action="store_true", help="Pilot only: prefer Together/SiliconFlow and exclude Wafer")
    args = parser.parse_args()
    sandbox = Path(__file__).resolve().parents[1] / "reports"
    output = args.output.resolve()
    if not output.is_relative_to(sandbox.resolve()):
        parser.error("Output must be within this checkout's reports directory")
    output.mkdir(parents=True, exist_ok=True)
    config.SEEN_LEDGER_PATH = str(output / "state" / "seen_items.json")
    config.ENRICHMENT_CACHE_DIR = str(output / "cache")
    data = json.loads((args.input / "analysis.json").read_text(encoding="utf-8"))
    original = copy.deepcopy(data)
    evidence = json.loads((args.input / "frozen_candidates.json").read_text(encoding="utf-8"))
    by_url = {i.get("canonical_url") or i["url"]: i for i in evidence}
    articles = [i for category in ("reports", "events", "podcasts") for i in data[category]]
    source_items = [article_scope.scoped_item(by_url[i["url"]]) for i in articles]
    (output / "source_evidence.json").write_text(json.dumps(source_items, indent=2, ensure_ascii=False), encoding="utf-8")
    run = scan_runtime.ScanRun(output, args.date, historical=True)
    start = time.monotonic()
    config.REVIEW_REASONING_EFFORT = "low"
    with scan_runtime.activate(run):
        records = topic_classifier.evaluate(source_items, run)
        classification_seconds = time.monotonic() - start
        (run.output_dir / "jev_phase.json").write_text(json.dumps({"classification_seconds": round(classification_seconds, 3), "decisions": records}, indent=2, ensure_ascii=False), encoding="utf-8")
        run.save_manifest("topic_classified")
        comparison = []
        for article, record in zip(articles, records):
            topic_classifier.apply_to_article(article, record)
        confirmation_start = time.monotonic()
        provider_options = {"order": ["together", "siliconflow"], "ignore": ["wafer"]} if args.prefer_providers else None
        reviews = topic_review.confirm(articles, source_items, records, args.model, run, provider_options) if args.confirm else []
        confirmation_seconds = time.monotonic() - confirmation_start
        for article, record in zip(articles, records):
            old, proposed = set(article["glm_tags"]), set(record.get("proposed_tags", []))
            comparison.append({"title": article["title"], "url": article["url"],
                "glm_tags": article["glm_tags"], "jev_tags": record.get("proposed_tags", []),
                "draft_tags": article["tags"], "added": sorted(proposed - old), "removed": sorted(old - proposed),
                "uncertain_topics": record.get("uncertain_topics", []), "origin": article["topic_tag_origin"]})
        # Verify this is strictly a metadata experiment: summaries/inclusion/date/URLs unchanged.
        restored = copy.deepcopy(data)
        for category in ("reports", "events", "podcasts"):
            for old, new in zip(original[category], restored[category]):
                new["tags"] = old["tags"]
                for name in ("glm_tags", "topic_classification", "topic_tag_origin", "topic_confirmation", "topic_review_required", "topic_check_proposed_tags"):
                    new.pop(name, None)
        if restored != original:
            raise RuntimeError("Topic pilot unexpectedly changed report content")
        data["topic_pilot"] = {"note": "Shadow topic test of 11 saved articles. Original GLM tags, relevance decisions and summaries are retained; Jev and evidence-check suggestions are audit-only pending reader calibration."}
        requests = getattr(run, "model_requests", [])
        audit = {"evaluation": True, "review_selection": {"total_enriched_candidates": len(evidence),
                  "selected_for_review": len(evidence), "skipped_before_review": 0},
                 "enriched_candidate_summary": {"date_status": dict(Counter(i.get("date_status", "unknown") for i in evidence))}}
        # Reused GLM report metrics remain labelled cached; new Jev metrics are separately reported.
        data["run"] = original.get("run", {})
        (output / "draft.html").write_text(editorial_report.generate_html(data, args.date, {}, recall_audit=audit), encoding="utf-8")
        editorial_report.generate_pdf(data, args.date, {}, str(output / "draft.pdf"), recall_audit=audit)
        summary = {"sample_size": len(articles), "questions_per_article": len(topic_classifier.questions()),
            "topic_judgments": sum(len(r.get("topics", [])) for r in records),
            "classification_seconds": round(classification_seconds, 3), "total_seconds_including_render": round(time.monotonic() - start, 3),
            "confirmation_seconds": round(confirmation_seconds, 3), "confirmation_failures": sum(r["status"] == "fallback" for r in reviews),
            "confirmation_not_required": sum(r["status"] == "not_required" for r in reviews),
            "topic_review_required": sum(i["topic_review_required"] for i in articles), "provider_preferences": provider_options,
            "trimmed_article_count": sum(bool(i.get("article_scope")) for i in source_items),
            "removed_tail_chars": sum(i.get("article_scope", {}).get("removed_chars", 0) for i in source_items),
            "requests_including_retries": len(requests), "cache_hits": sum(r["cache_status"] == "hit" for r in records),
            "fallbacks": sum(r["status"] == "fallback" for r in records),
            "uncertain_judgments": sum(len(r.get("uncertain_topics", [])) for r in records),
            "exact_agreement_with_glm": sum(set(c["glm_tags"]) == set(c["jev_tags"]) for c in comparison),
            "glm_tag_count": sum(len(c["glm_tags"]) for c in comparison), "jev_tag_count": sum(len(c["jev_tags"]) for c in comparison),
            "draft_tag_count": sum(len(c["draft_tags"]) for c in comparison),
            "evidence_check_proposed_tag_count": sum(len(i.get("topic_check_proposed_tags", i["tags"])) for i in articles),
            "shadow_mode": True,
            "reported_cost_usd": sum((r.get("usage") or {}).get("cost", 0) or 0 for r in requests),
            "cost_by_purpose_usd": {purpose: sum((r.get("usage") or {}).get("cost", 0) or 0 for r in requests if r["purpose"] == purpose) for purpose in {r["purpose"] for r in requests}},
            "requests_missing_cost": sum((r.get("usage") or {}).get("cost") is None for r in requests),
            "input_tokens": sum((r.get("usage") or {}).get("input_tokens", (r.get("usage") or {}).get("prompt_tokens", 0)) or 0 for r in requests),
            "output_tokens": sum((r.get("usage") or {}).get("output_tokens", (r.get("usage") or {}).get("completion_tokens", 0)) or 0 for r in requests),
            "served_models": sorted({r["actual_model"] for r in requests if r.get("actual_model")}),
            "reader_labelled_accuracy": None, "email_sent": False,
            "relevance_source": str(args.input.resolve()), "fresh_source_scan": False}
        for name, value in (("analysis.json", data), ("topic_decisions.json", records), ("comparison.json", comparison),
                            ("summary.json", summary), ("questions.json", topic_classifier.questions()), ("confirmations.json", reviews)):
            (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        with (output / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(comparison[0]) if comparison else ["title"])
            writer.writeheader()
            writer.writerows({k: "; ".join(v) if isinstance(v, list) else v for k, v in row.items()} for row in comparison)
        # Blank human labels, no model suggestions: permits an independent reader benchmark.
        with (output / "reader_labels.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["title", "url", "topic", "include", "exclude", "reader_coverage"])
            writer.writeheader()
            writer.writerows(dict(title=i["title"], url=i["url"], topic=t, include=r["include"], exclude=r["exclude"], reader_coverage="") for i in articles for t, r in config.TOPIC_ONTOLOGY.items())
        run.save_manifest("topic_evaluation_complete", email_sent=False)
        (run.output_dir / "evaluation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
