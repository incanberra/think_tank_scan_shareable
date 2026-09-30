import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
import editorial_report
import jev_client
import topic_classifier as classifier
import topic_review
from topic_badges import badges, PALETTE, TopicPills


def result():
    return {"answers": {name: {"type": "choice", "choice": "absent", "confidence": 1.0,
        "probabilities": {k: float(k == "absent") for k in question["criteria"]}}
        for name, question in classifier.questions().items()}}


def set_answer(value, topic, probabilities):
    key = list(classifier.questions())[list(config.TOPIC_ONTOLOGY).index(topic)]
    answer = value["answers"][key]
    answer["probabilities"] = probabilities
    answer["choice"] = max(probabilities, key=probabilities.get)


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.item = {"url": "https://example.org/article", "title": "Source", "extracted_text": "Critical minerals refining supply resilience. " * 30}

    def test_all_topics_have_independent_rules_and_fixed_colours(self):
        self.assertEqual(len(classifier.questions()), 14)
        self.assertEqual(set(PALETTE), set(config.TOPIC_ONTOLOGY))
        for topic, q in zip(config.TOPIC_ONTOLOGY, classifier.questions().values()):
            self.assertIn(config.TOPIC_ONTOLOGY[topic]["include"], q["instructions"])
            self.assertIn(config.TOPIC_ONTOLOGY[topic]["exclude"], q["instructions"])

    def test_multiple_material_topics_while_passing_mentions_are_excluded(self):
        value = result()
        for topic, role in (("Critical Minerals", "central"), ("Energy Independence and Transition", "supporting"), ("Food and Water Security", "incidental")):
            set_answer(value, topic, {k: float(k == role) for k in ("central", "supporting", "incidental", "absent", "insufficient")})
        rows = classifier.classify_result(value, classifier.questions())
        self.assertEqual([r["topic"] for r in rows if r["decision"] == "material"], ["Critical Minerals", "Energy Independence and Transition"])

    def test_uncertainty_preserves_previous_glm_tag_and_relevance(self):
        article = {"tags": ["Economic Coercion"], "summary": "Unchanged", "editorial_tier": "main"}
        classifier.apply_to_article(article, {"status": "success", "proposed_tags": ["Critical Minerals"], "uncertain_topics": ["Economic Coercion"]})
        self.assertEqual(article["tags"], ["Critical Minerals", "Economic Coercion"])
        self.assertEqual(article["summary"], "Unchanged")
        self.assertEqual(article["topic_tag_origin"], "Jev with GLM fallback")

    def test_partial_or_failed_evidence_preserves_tags(self):
        for record in ({"status": "fallback"}, {"status": "success", "partial_evidence": True}):
            article = {"tags": ["Critical Minerals"]}
            classifier.apply_to_article(article, record)
            self.assertEqual(article["tags"], ["Critical Minerals"])

    def test_thin_evidence_does_not_call_api_and_packets_do_not_leak_glm_labels(self):
        with patch.object(jev_client, "decide") as call:
            self.assertEqual(classifier.evaluate([dict(self.item, extracted_text="teaser")])[0]["status"], "fallback")
        call.assert_not_called()
        packet = classifier.packet(dict(self.item, tags=["Secret label"], summary="Secret summary", topic_hints=["Secret hint"]))
        self.assertNotIn("Secret", json.dumps(packet))

    def test_cache_is_reused_and_invalidated_by_source_model_and_ontology(self):
        run = SimpleNamespace(state={}, reprocess=False, persist=lambda: None, model_requests=[])
        with patch.object(jev_client, "decide", return_value=result()) as call:
            classifier.evaluate([self.item], run)
            self.assertEqual(classifier.evaluate([self.item], run)[0]["cache_status"], "hit")
            call.assert_called_once()
        before = classifier.cache_key(self.item, classifier.questions())
        self.assertNotEqual(before, classifier.cache_key(dict(self.item, extracted_text="different"), classifier.questions()))
        with patch.object(config, "TRIAGE_MODEL", "changed"):
            self.assertNotEqual(before, classifier.cache_key(self.item, classifier.questions()))
        rubric = copy.deepcopy(classifier.questions())
        rubric["topic_00"]["instructions"] += "Changed rule"
        self.assertNotEqual(before, classifier.cache_key(self.item, rubric))

    def test_invalid_topic_response_fails_closed(self):
        value = result()
        del value["answers"]["topic_13"]
        with patch.object(jev_client, "decide", return_value=value):
            self.assertEqual(classifier.evaluate([self.item])[0]["status"], "fallback")

    def test_pills_limit_does_not_discard_audit_tags_and_wraps(self):
        article = {"tags": list(PALETTE) + ["<script>bad</script>"]}
        pills, hidden = badges(article)
        self.assertEqual((len(pills), hidden), (3, 11))
        self.assertEqual(len(article["tags"]), 15)
        flow = TopicPills(pills, hidden)
        width, height = flow.wrap(200, 800)
        self.assertGreater(height, 23)
        self.assertTrue(all(x + w <= width for _, x, _, w in flow.placements))

    def test_html_escapes_title_and_pdf_contains_pill_labels(self):
        item = {"title": "<script>source</script>", "tags": ["Critical Minerals"], "institution": "Example", "summary": "Source summary", "url": "javascript:bad"}
        data = {"reports": [item]}
        html = editorial_report.generate_html(data, "2026-09-30", {}, recall_audit={"evaluation": True})
        self.assertIn("Critical minerals", html)
        self.assertNotIn("<script>source</script>", html)
        self.assertNotIn("javascript:bad", html)
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "report.pdf"
            editorial_report.generate_pdf(data, "2026-09-30", {}, str(pdf), recall_audit={"evaluation": True})
            from pypdf import PdfReader
            self.assertIn("Critical minerals", "".join(p.extract_text() for p in PdfReader(pdf).pages))

    def test_confirmation_requires_valid_topics_roles_and_grounded_quotes(self):
        good = {"topics": [{"topic": "Critical Minerals", "role": "central", "evidence": ["Critical minerals refining supply resilience."]}]}
        self.assertEqual(topic_review.validate(good, ["Critical Minerals"], self.item), good["topics"])
        for change in ({"evidence": ["Invented claims about mineral supply not in the source."]}, {"role": "maybe"}, {"topic": "Unknown"}):
            row = dict(good["topics"][0], **change)
            with self.assertRaises(ValueError):
                topic_review.validate({"topics": [row]}, ["Critical Minerals"], self.item)

    def test_confirmed_central_topic_is_shown_before_raw_jev_guess(self):
        item = {"tags": ["Critical Minerals", "Energy Independence and Transition"],
                "topic_classification": {"topics": [{"topic": "Critical Minerals", "role": "central", "material_probability": 1}]},
                "topic_confirmation": {"status": "success", "topics": [{"topic": "Critical Minerals", "role": "supporting"},
                    {"topic": "Energy Independence and Transition", "role": "central"}]}}
        self.assertEqual(badges(item)[0][0]["topic"], "Energy Independence and Transition")


if __name__ == "__main__":
    unittest.main()
