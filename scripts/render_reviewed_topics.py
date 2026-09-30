"""Render explicit analyst topic adjudications over a saved pilot (no API calls)."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import editorial_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    args = parser.parse_args()
    root = args.pilot.resolve()
    if not root.is_relative_to((Path(__file__).resolve().parents[1] / "reports").resolve()):
        parser.error("Pilot directory must be within this checkout's reports")
    data = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
    review = json.loads((root / "analyst_review.json").read_text(encoding="utf-8"))
    rows = {r["title"]: r for r in review["articles"]}
    items = [i for category in ("reports", "events", "podcasts") for i in data[category]]
    if set(rows) != {i["title"] for i in items} or len(rows) != len(items):
        raise ValueError("Analyst review must cover exactly the included articles")
    for item in items:
        row = rows[item["title"]]
        if not row["topics"] or any(r["topic"] not in config.TOPIC_ONTOLOGY or r["role"] not in ("central", "supporting") for r in row["topics"]):
            raise ValueError("Invalid analyst topics")
        item["automated_draft_tags"] = item["tags"]
        item["tags"] = [r["topic"] for r in row["topics"]]
        item["topic_confirmation"] = {"status": "success", "topics": row["topics"], "method": "source-based analyst adjudication", "basis": row["basis"]}
        item["topic_tag_origin"] = "Source-reviewed pilot"
    data["topic_pilot"] = {"note": "Topic-label test: 11 included articles reused from the saved 30-candidate replay. Labels in this draft were reviewed against source text; no new discovery or relevance review."}
    data["run"] = {"run_id": "jev-topic-pilot-reviewed", "coverage_end": "2026-09-30T03:00:00+10:00"}
    audit = {"evaluation": True, "review_selection": {"total_enriched_candidates": 30, "selected_for_review": 30, "skipped_before_review": 0}}
    (root / "reviewed-draft.html").write_text(editorial_report.generate_html(data, review["date"], {}, recall_audit=audit), encoding="utf-8")
    editorial_report.generate_pdf(data, review["date"], {}, str(root / "reviewed-draft.pdf"), recall_audit=audit)
    (root / "reviewed-analysis.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    proposed = [set(i["topic_classification"]["proposed_tags"]) for i in items]
    reference = [set(i["tags"]) for i in items]
    metrics = {"adjudicated_articles": len(items), "adjudicated_tag_count": sum(map(len, reference)),
        "raw_jev_tags_retained_by_analyst": sum(len(j & r) for j, r in zip(proposed, reference)),
        "raw_jev_tags_not_retained": sum(len(j - r) for j, r in zip(proposed, reference)),
        "additional_tags_identified_by_analyst": sum(len(r - j) for j, r in zip(proposed, reference)),
        "exact_jev_agreement_with_analyst": sum(j == r for j, r in zip(proposed, reference)),
        "reader_labelled_accuracy": None, "additional_api_cost_usd": 0}
    (root / "assessment.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
