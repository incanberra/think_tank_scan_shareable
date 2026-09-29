import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import analyzer
import config
import content_extractor
import editorial_policy as policy
import jev_client
import triage
from test_scanner_reliability import publication


BODY = "Export restrictions on semiconductor equipment constrain advanced chip production. " * 12


def decision(centrality="core", mechanism=0.99, adequacy="body"):
    choices = {"centrality": centrality, "purpose": "analysis", "adequacy": adequacy}
    answers = {name: {"type": "choice", "choice": choice, "confidence": 1.0,
        "probabilities": {k: float(k == choice) for k in jev_client.QUESTIONS[name]["criteria"]}} for name, choice in choices.items()}
    answers["mechanism"] = {"type": "noul", "noul": mechanism}
    return {"model": "typesafe/jev-1.13-test", "answers": answers, "usage": {"cost": 0.0001}}


def positive():
    return {"temp_id": 0, "is_material_match": True, "needs_review": False,
            "matched_topics": [config.ECONOMIC_SECURITY_TOPICS[0]], "editorial_tier": "main",
            "summary": "Export restrictions constrain advanced chip production.",
            "evidence": ["Export restrictions on semiconductor equipment constrain advanced chip production."]}


class JevTests(unittest.TestCase):
    def setUp(self):
        self.item = publication(extracted_text=BODY, extracted_text_chars=len(BODY))

    def test_distribution_and_types_fail_closed(self):
        for value in (float("nan"), float("inf"), True, -0.1, 1.1):
            result = decision()
            result["answers"]["mechanism"]["noul"] = value
            with self.assertRaises(ValueError): jev_client.validate(result)
        result = decision()
        result["answers"]["centrality"]["probabilities"]["absent"] = 0.6
        with self.assertRaises(ValueError): jev_client.validate(result)

    def test_endpoint_is_decisions_not_chat_and_usage_is_recorded(self):
        run = Mock(model_requests=[])
        response = Mock(status_code=200, json=lambda: decision())
        with patch.object(config, "OPENROUTER_API_KEY", "test"), patch.object(jev_client.requests, "post", return_value=response) as call:
            jev_client.decide(policy.packet(self.item), run)
        self.assertEqual(call.call_args.args[0], jev_client.ENDPOINT)
        self.assertEqual(set(call.call_args.kwargs["json"]["questions"]), set(jev_client.QUESTIONS))
        self.assertEqual(run.model_requests[0]["status"], "success")

    def test_auth_failure_has_no_retry_and_falls_back(self):
        response = Mock(status_code=401)
        with patch.object(config, "TRIAGE_MODE", "active"), patch.object(config, "OPENROUTER_API_KEY", "test"), patch.object(jev_client.requests, "post", return_value=response) as call:
            result = triage.evaluate([self.item])[0]
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["effective_route"], "glm")
        self.assertEqual(result["proposed_route"], "fallback")

    def test_conflict_and_partial_body_cannot_auto_exclude(self):
        self.assertEqual(triage.route(self.item, decision("absent", 0.8))[0], "glm")
        self.assertEqual(triage.route(dict(self.item, extracted_text=BODY * 90), decision("absent", 0.0))[0], "glm")
        self.assertEqual(triage.route(self.item, decision("absent", 0.0))[0], "exclude")
        self.assertEqual(triage.route(self.item, decision("core", 0.99, "teaser"))[0], "hold")

    def test_shadow_records_proposal_without_changing_routing(self):
        with patch.object(config, "TRIAGE_MODE", "shadow"), patch.object(jev_client, "decide", return_value=decision("absent", 0.0)):
            result = triage.evaluate([self.item])[0]
        self.assertEqual(result["proposed_route"], "exclude")
        self.assertEqual(result["effective_route"], "glm")

    def test_policy_text_and_model_changes_invalidate_cache(self):
        before = triage.cache_key(self.item)
        with patch.object(policy, "RUBRIC_VERSION", "changed"):
            self.assertNotEqual(before, triage.cache_key(self.item))
        with patch.object(config, "TRIAGE_MODEL", "other"):
            self.assertNotEqual(before, triage.cache_key(self.item))
        self.assertNotEqual(before, triage.cache_key(dict(self.item, extracted_text=BODY + "changed")))

    def test_only_missing_row_gets_one_targeted_repair(self):
        other = dict(self.item, url="https://example.org/reports/other", title="Another semiconductor research study")
        first = positive()
        with patch.object(config, "TRIAGE_MODE", "off"), patch.object(config, "OPENROUTER_API_KEY", "test"), patch.object(analyzer.ai_client, "generate_json_with_retry", side_effect=[{"analyses": [first]}, {"analyses": [{"temp_id": 0, "is_material_match": False}]}]) as call:
            data = analyzer.analyze_items([self.item, other])
        self.assertEqual(len(data["reports"]), 1)
        self.assertEqual(len(data["excluded"]), 1)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args.kwargs["purpose"], "schema_repair")
        self.assertNotIn(self.item["title"], call.call_args.kwargs["messages"][0]["content"])

    def test_invented_quotes_or_invalid_topics_cannot_be_published(self):
        for change in ({"evidence": ["Invented substantive claims not found in the article body."]}, {"matched_topics": ["AI"]}):
            row = dict(positive(), **change)
            with patch.object(config, "TRIAGE_MODE", "off"), patch.object(config, "OPENROUTER_API_KEY", "test"), patch.object(analyzer.ai_client, "generate_json_with_retry", return_value={"analyses": [row]}):
                data = analyzer.analyze_items([self.item])
            self.assertEqual(data["reports"], [])
            self.assertEqual(len(data["needs_review"]), 1)

    def test_typographic_quotes_and_enclosing_marks_do_not_hide_a_valid_article(self):
        text = "Europe’s energy-security policy reduces Russia’s economic leverage. " * 15
        row = dict(positive(), evidence=['"Europe\'s energy-security policy reduces Russia\'s economic leverage."'])
        self.assertEqual(policy.validation_error(dict(self.item, extracted_text=text), row), "")
        row["evidence"] = ["Europe's energy-security policy...Russia's economic leverage."]
        self.assertTrue(policy.validation_error(dict(self.item, extracted_text=text), row))
        text = "Sanctions restrict exports—some shipments cross borders -despite controls . " * 12
        row["evidence"] = ["Sanctions restrict exports-some shipments cross borders-despite controls."]
        self.assertEqual(policy.validation_error(dict(self.item, extracted_text=text), row), "")
        row["evidence"] = ["Sanctions allow exports-some shipments cross borders-despite controls."]
        self.assertTrue(policy.validation_error(dict(self.item, extracted_text=text), row))

    def test_dates_and_delivered_history_block_both_models(self):
        for change in ({"last_reported_run_date": "2026-09-07"}, {"date_status": "verified_out_of_window"}):
            with patch.object(config, "TRIAGE_MODE", "active"), patch.object(jev_client, "decide") as jev, patch.object(analyzer.ai_client, "generate_json_with_retry") as glm:
                data = analyzer.analyze_items([dict(self.item, **change)])
            jev.assert_not_called(); glm.assert_not_called()
            self.assertEqual(len(data["needs_review"]), 1)

    def test_thin_article_is_held_but_substantive_event_description_is_allowed(self):
        text = "Critical mineral supply discussion. " * 12
        self.assertEqual(policy.body_quality(dict(self.item, extracted_text=text)), "insufficient")
        self.assertEqual(policy.body_quality(dict(self.item, extracted_text=text, item_type="event")), "sufficient")

    def test_cancelled_event_cannot_reach_either_model(self):
        item = dict(self.item, item_type="event", title="CANCELED: Critical minerals discussion")
        with patch.object(config, "TRIAGE_MODE", "active"), patch.object(jev_client, "decide") as jev, patch.object(analyzer.ai_client, "generate_json_with_retry") as glm:
            data = analyzer.analyze_items([item])
        jev.assert_not_called(); glm.assert_not_called()
        self.assertEqual(data["excluded"][0]["exclusion_reason"], "cancelled_event")

    def test_storage_ceiling_is_treated_as_incomplete(self):
        item = dict(self.item, extracted_text="x" * config.TEXT_STORAGE_CHAR_LIMIT)
        self.assertTrue(triage.evidence_packet(item)["excerpt_is_partial"])

    def test_jev_gets_full_long_body_while_glm_keeps_small_packet(self):
        item = dict(self.item, extracted_text=BODY * 30)
        self.assertFalse(triage.evidence_packet(item)["excerpt_is_partial"])
        self.assertTrue(policy.packet(item)["excerpt_is_partial"])

    def test_related_jsonld_body_is_not_borrowed(self):
        graph = [{"@type": "Article", "url": "https://example.org/related", "articleBody": BODY},
                 {"@type": "Article", "url": "https://example.org/current", "description": "A long teaser. " * 40}]
        html = '<link rel="canonical" href="https://example.org/current"><script type="application/ld+json">' + json.dumps(graph) + '</script><article><p>Short current article teaser.</p></article>'
        self.assertNotIn("Export restrictions", content_extractor.extract_text_from_html(html))

    def test_nested_publisher_paragraphs_do_not_double_the_article(self):
        opening = "Strategic dependence on mineral imports increases vulnerability to export restrictions."
        ending = "Diversified processing capacity can reduce exposure to coercion and supply disruptions."
        body = content_extractor.extract_text_from_html('<article><p>' + opening + '<p>' + ending + '</p></p></article>')
        self.assertEqual(body.count(ending), 1)
        self.assertIn(opening, body)

    def test_lowy_embedded_body_keeps_references_but_not_related_articles(self):
        node = ["$", "div", None, {"className": "prose dark:prose-invert", "children": ["$L2"]}]
        paragraph = ["$", "p", None, {"children": [BODY]}]
        chunks = "1:" + json.dumps(node) + "\n2:" + json.dumps(paragraph) + "\n3:" + json.dumps({"menu": "RELATED ARTICLE TEXT"})
        script = "self.__next_f.push(" + json.dumps([1, chunks]) + ")"
        html = '<link rel="canonical" href="https://www.lowyinstitute.org/the-interpreter/test"><main><p>Short teaser.</p></main><script>' + script + "</script>"
        body = content_extractor.extract_text_from_html(html)
        self.assertIn("Export restrictions", body)
        self.assertNotIn("RELATED ARTICLE", body)
        self.assertNotIn("Short teaser", body)


if __name__ == "__main__": unittest.main()
