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
import article_scope
import content_extractor
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
        self.assertEqual(article["tags"], ["Economic Coercion"])
        self.assertEqual(article["summary"], "Unchanged")
        self.assertEqual(article["topic_tag_origin"], "GLM retained pending topic check")
        self.assertTrue(article["topic_review_required"])

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

    def test_confident_disagreement_cannot_silently_remove_or_add_tags(self):
        for proposed in ([], ["Emerging Technologies"]):
            article = {"tags": ["Critical Minerals"]}
            record = {"status": "success", "proposed_tags": proposed, "uncertain_topics": []}
            classifier.apply_to_article(article, record)
            self.assertEqual(article["tags"], ["Critical Minerals"])
            self.assertTrue(article["topic_review_required"])
            self.assertIn("Critical Minerals", record["review_topics"])

    def test_biography_boundary_removes_related_cards_only_on_known_publisher(self):
        body = self.item["extracted_text"]
        for bio in ("Valbona Zeneli , PhD, is a nonresident senior fellow at the Atlantic Council.",
                    "Jeff Lightfoot is senior director at the Center for International Private Enterprise."):
            text = body + "\n\n" + bio + "\n\nRelated: China's rare-earth sanctions and cyber espionage."
            cleaned, audit = article_scope.trim(text, "https://www.atlanticcouncil.org/article/")
            self.assertEqual(cleaned, body.rstrip())
            self.assertGreater(audit["removed_chars"], 0)
            self.assertEqual(article_scope.trim(text, "https://example.org/article/")[0], text)
            self.assertEqual(article_scope.trim(text, "https://www.atlanticcouncil.org.evil.example/article/")[0], text)
        # Do not cut ordinary prose or a very thin current article at a biography.
        text = "Europe is a senior partner of the Atlantic Council. " * 15
        self.assertEqual(article_scope.trim(text, "https://www.atlanticcouncil.org/article/")[0], text)
        text = "Short body\n\nJane Smith is a senior fellow at the Atlantic Council."
        self.assertEqual(article_scope.trim(text, "https://www.atlanticcouncil.org/article/")[0], text)
        text = body + "\n\nJane Smith is a senior fellow at the Atlantic Council.\n\n" + body * 3
        self.assertEqual(article_scope.trim(text, "https://www.atlanticcouncil.org/article/")[0], text)

    def test_real_html_extraction_removes_tail_and_stale_cache_is_invalidated(self):
        html = '<link rel="canonical" href="https://www.atlanticcouncil.org/article/"><article><p>' + self.item["extracted_text"] + '</p><p>Jane Smith is a nonresident senior fellow at the Atlantic Council.</p><p>Related: Sanctions and export-control chokepoints.</p></article>'
        text = content_extractor.extract_text_from_html(html)
        self.assertNotIn("Related:", text)
        self.assertIn("Critical minerals", text)
        with tempfile.TemporaryDirectory() as directory, patch.object(config, "ENRICHMENT_CACHE_DIR", directory), patch.object(config, "ENABLE_ENRICHMENT_CACHE", True):
            path = Path(content_extractor.cache_path_for_url(self.item["url"]))
            path.write_text(json.dumps({"schema_version": 6, "page_data": {"extracted_text": "old polluted body"}}))
            self.assertIsNone(content_extractor.load_cache_entry(self.item["url"]))

    def test_confirmation_only_reviews_flagged_topics_and_preserves_agreement(self):
        article = {"url": self.item["url"], "tags": ["Critical Minerals"], "glm_tags": ["Critical Minerals"]}
        record = {"status": "success", "review_topics": ["Economic Coercion"], "proposed_tags": ["Critical Minerals"]}
        reply = {"topics": [{"topic": "Economic Coercion", "role": "not_material", "reason": "No qualifying economic tool", "evidence": []}]}
        run = SimpleNamespace(model_requests=[])
        with patch('topic_review.ai_client.generate_json_with_retry', return_value=reply) as call:
            reviews = topic_review.confirm([article], [self.item], [record], "model", run, {"ignore": ["wafer"]})
        self.assertEqual(reviews[0]["requested_topics"], ["Economic Coercion"])
        self.assertEqual(article["tags"], ["Critical Minerals"])
        self.assertFalse(article["topic_review_required"])
        self.assertEqual(call.call_args.kwargs["provider_options"], {"ignore": ["wafer"]})

    def test_agreement_skips_api_and_bad_confirmation_gets_one_repair(self):
        article = {"url": self.item["url"], "tags": ["Critical Minerals"], "glm_tags": ["Critical Minerals"]}
        run = SimpleNamespace(model_requests=[])
        with patch('topic_review.ai_client.generate_json_with_retry') as call:
            review = topic_review.confirm([article], [self.item], [{"status": "success", "review_topics": []}], "model", run)
            self.assertEqual(review[0]["status"], "not_required")
            call.assert_not_called()
        record = {"status": "success", "review_topics": ["Critical Minerals"]}
        invalid = {"topics": []}
        good = {"topics": [{"topic": "Critical Minerals", "role": "central", "evidence": ["Critical minerals refining supply resilience."]}]}
        with patch('topic_review.ai_client.generate_json_with_retry', side_effect=[invalid, good]) as call:
            review = topic_review.confirm([article], [self.item], [record], "model", run)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(call.call_args.kwargs["purpose"], "topic_confirmation_repair")
            self.assertEqual(review[0]["status"], "success")
            self.assertIn("raw_result", review[0]["attempts"][0])

    def test_schema_names_are_complete_and_unknown_keys_fail_closed(self):
        schema = topic_review.response_schema(["Critical Minerals", "Economic Coercion"])
        self.assertEqual(schema["properties"]["topics"]["required"], ["Critical Minerals", "Economic Coercion"])
        self.assertFalse(schema["properties"]["topics"]["additionalProperties"])
        result = {"topics": {"Critical Minerals": {"role": "central", "reason": "Substantive refining", "evidence": ["Critical minerals refining supply resilience."]}}}
        self.assertEqual(topic_review.validate(result, ["Critical Minerals"], self.item)[0]["topic"], "Critical Minerals")
        result["topics"]["invented"] = result["topics"]["Critical Minerals"]
        with self.assertRaises(ValueError):
            topic_review.validate(result, ["Critical Minerals"], self.item)

    def test_shadow_confirmation_does_not_apply_new_tags_even_with_valid_quotes(self):
        article = {"url": self.item["url"], "tags": ["Energy Independence and Transition"], "glm_tags": ["Energy Independence and Transition"]}
        record = {"status": "success", "review_topics": ["Critical Minerals"]}
        reply = {"topics": {"Critical Minerals": {"role": "central", "reason": "Substantive refining", "evidence": ["Critical minerals refining supply resilience."]}}}
        with patch('topic_review.ai_client.generate_json_with_retry', return_value=reply):
            topic_review.confirm([article], [self.item], [record], "model", SimpleNamespace(model_requests=[]))
        self.assertEqual(article["tags"], ["Energy Independence and Transition"])
        self.assertIn("Critical Minerals", article["topic_check_proposed_tags"])
        self.assertTrue(article["topic_review_required"])


if __name__ == "__main__":
    unittest.main()
