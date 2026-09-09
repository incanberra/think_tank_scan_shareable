"""Offline regression tests. Run: python -m unittest test_scanner_reliability -v"""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

import analyzer
import candidate_selector
import config
import content_extractor
import eligibility
import main
import seen_ledger
import rss_parser
import source_discovery
import state_storage


def publication(**overrides):
    return {
        "title": "A specific research publication on AI governance",
        "url": "https://example.org/reports/ai-governance",
        "institution": "Example Institute",
        "date_status": "verified_in_window",
        "date_source": "page_publication",
        "published_at_verified": "2026-09-07T10:00:00+10:00",
        "scan_run_date": "2026-09-08",
        "extracted_text": "Research evidence and analysis.",
        "extracted_text_chars": 1000,
        "topic_hints": [],
        "item_type": "report",
        **overrides,
    }


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger_patch = patch.object(config, "SEEN_LEDGER_PATH", str(self.root / "state" / "seen.json"))
        self.ledger_patch.start()
        self.addCleanup(self.ledger_patch.stop)
        self.cache_patch = patch.object(config, "ENRICHMENT_CACHE_DIR", str(self.root / "cache"))
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def annotate(self, item, day="2026-09-08"):
        seen_ledger.annotate_items_with_seen_metadata([item], str(self.root), day)
        return item

    def report(self, item, day="2026-09-08"):
        seen_ledger.mark_reported_items(str(self.root), day, "test", {"reports": [item]})

    def page(self, html, headers=None, url="https://example.org/reports/ai-governance"):
        response = Mock(status_code=200, url=url, text=html, headers={"content-type": "text/html", **(headers or {})})
        response.content = html.encode()
        with patch.object(content_extractor.requests, "get", return_value=response):
            return content_extractor.extract_page(url)

    def test_consecutive_runs_and_same_day_rerun_do_not_repeat(self):
        first = self.annotate(publication())
        self.report(first)
        for day in ("2026-09-08", "2026-09-09"):
            for method in ("rss", "sitemap"):
                with self.subTest(day=day, method=method):
                    again = self.annotate(publication(discovery_methods=[method]), day)
                    self.assertEqual(candidate_selector.should_review_candidate(again), (False, "already_reported"))

    def test_changed_content_does_not_republish(self):
        self.report(self.annotate(publication()))
        again = self.annotate(publication(extracted_text="Changed research content."), "2026-09-09")
        self.assertTrue(again["content_changed_since_last_seen"])
        self.assertFalse(candidate_selector.should_review_candidate(again)[0])

    def test_seen_but_never_reported_remains_eligible(self):
        self.annotate(publication())
        again = self.annotate(publication(), "2026-09-09")
        self.assertEqual(again["seen_status"], "seen_before")
        self.assertTrue(candidate_selector.should_review_candidate(again)[0])

    def test_sampling_cannot_override_report_history(self):
        item = publication(date_status="date_unknown", last_reported_run_date="2026-09-07", content_changed_since_last_seen=True)
        chosen, _ = candidate_selector.select_candidates_for_review([item])
        self.assertEqual(chosen, [])

    def test_final_gate_uses_persisted_history(self):
        original = self.annotate(publication())
        self.report(original)
        untrusted = publication()  # Model output omitted the history fields.
        data = {"reports": [untrusted]}
        eligibility.filter_for_publication(data, str(self.root), "2026-09-09")
        self.assertEqual(data["reports"], [])
        self.assertEqual(data["excluded"][0]["exclusion_reason"], "already_reported")

    def test_comparison_models_can_share_current_run_items(self):
        item = self.annotate(publication())
        self.report(item)
        data = {"reports": [copy.deepcopy(item)]}
        eligibility.filter_for_publication(data, str(self.root), "2026-09-08")
        self.assertEqual(len(data["reports"]), 1)

    def test_http_modified_date_is_not_publication(self):
        page = self.page("<html><article><h1>AI report</h1><p>Research findings.</p></article></html>", {"Last-Modified": "Mon, 07 Sep 2026 09:04:28 GMT"})
        with patch.object(content_extractor, "extract_page", return_value=page):
            item = content_extractor.enrich_item({"url": "https://example.org/reports/ai-governance", "discovery_methods": ["sitemap"]}, "2026-09-08")
        self.assertEqual(item["date_status"], "date_unknown")
        self.assertEqual(item["published_at_verified"], "")
        self.assertTrue(item["modified_at"])

    def test_sitemap_lastmod_stays_separate(self):
        item = source_discovery.candidate_from_url({"name": "Example", "domain": "example.org"}, "https://example.org/reports/one", "sitemap", date_value="2026-09-07")
        self.assertIsNone(item["published_at"])
        self.assertTrue(item["modified_at"])
        result = content_extractor.enrich_item(item, "2026-09-08", fetch_pages=False)
        self.assertEqual(result["date_status"], "date_unknown")

    def test_atom_updated_only_is_discovery_evidence_and_query_ids_survive(self):
        xml = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>AI analysis</title><link href="https://example.org/?p=123&amp;utm_source=feed"/><updated>2026-09-07T10:00:00Z</updated></entry></feed>'
        session = Mock()
        session.get.return_value = Mock(status_code=200, content=xml)
        with patch.object(rss_parser, "RSS_FEEDS", {"Example": "https://example.org/feed"}), patch.object(rss_parser.requests, "Session", return_value=session):
            items, _ = rss_parser.fetch_rss_items("2026-09-08")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.org/?p=123")
        self.assertIsNone(items[0]["published_at"])
        result = content_extractor.enrich_item(items[0], "2026-09-08", fetch_pages=False)
        self.assertEqual(result["date_status"], "date_unknown")

    def test_original_publication_wins_over_html_modified(self):
        page = self.page('<meta property="article:modified_time" content="2026-09-07"><script type="application/ld+json">{"@type":"Article","datePublished":"2025-01-01","dateModified":"2026-09-07"}</script>')
        self.assertEqual(page["extracted_date_dt"].year, 2025)
        self.assertTrue(page["modified_at"].startswith("2026-09-07"))
        result = content_extractor.assess_date_status({}, page["extracted_date_dt"], "2026-09-08", 48)
        self.assertEqual(result["date_status"], "verified_out_of_window")

    def test_modified_only_json_ld_is_undated(self):
        soup = BeautifulSoup('<script type="application/ld+json">{"@type":"Article","dateModified":"2026-09-07","dateCreated":"2026-09-06"}</script>', "html.parser")
        self.assertFalse(content_extractor.extract_json_ld_metadata(soup)["date"])

    def test_pdf_metadata_does_not_establish_publication(self):
        response = Mock(status_code=200, content=b"pdf", url="https://example.org/paper.pdf")
        reader = Mock(metadata={"/ModDate": "D:20260907120000", "/CreationDate": "D:20240101000000"}, pages=[])
        with patch.object(content_extractor, "PdfReader", return_value=reader):
            result = content_extractor.extract_pdf_from_response(response, response.url)
        self.assertIsNone(result["extracted_date_dt"])
        self.assertTrue(result["modified_at"])

    def test_legacy_cache_is_refetched(self):
        url = "https://example.org/reports/ai-governance"
        path = Path(content_extractor.cache_path_for_url(url))
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"page_data": {"extracted_date": "07 September 2026"}}))
        self.assertIsNone(content_extractor.load_cache_entry(url))

    def test_events_need_verified_future_date_and_are_once_only(self):
        event = publication(item_type="event", date_status="date_unknown", event_start_at="2026-09-23T10:00:00+10:00")
        self.assertEqual(eligibility.exclusion_reason(event, True), "")
        self.assertEqual(eligibility.exclusion_reason({**event, "event_start_at": ""}), "event_date_unverified")
        self.assertEqual(eligibility.exclusion_reason({**event, "event_start_at": "2026-06-02"}), "past_event")
        self.assertEqual(eligibility.exclusion_reason({**event, "last_reported_run_date": "2026-09-07"}), "already_reported")

    def test_structured_event_dates_are_extracted_separately(self):
        page = self.page('<script type="application/ld+json">{"@type":"Event","startDate":"2026-09-23","datePublished":"2026-09-07"}</script>')
        self.assertTrue(page["event_start_at"].startswith("2026-09-23"))
        self.assertEqual(page["event_start_precision"], "date")
        self.assertEqual(page["extracted_date_dt"].day, 7)

    def test_listings_are_rejected_even_with_dates(self):
        for url in ("https://www.hudson.org/events", "https://example.org/reports", "https://www.sipri.org/research/armament-and-disarmament/dual-use-and-arms-trade-control", "https://www.cnas.org/research/technology-and-national-security/biotechnology", "https://www.rand.org/randeurope/research/projects/2024/governance-frameworks.html"):
            with self.subTest(url=url):
                self.assertTrue(eligibility.exclusion_reason(publication(url=url)))
        self.assertTrue(source_discovery.native_rejection_reason("https://example.org/events", allow_dated_listing=True))

    def test_index_date_not_borrowed_from_other_article(self):
        soup = BeautifulSoup('<div><a href="/a">One</a><a href="/b">Two</a><time datetime="2026-09-07">7 September</time></div>', "html.parser")
        self.assertIsNone(source_discovery.extract_date_near_anchor(soup.a))
        soup = BeautifulSoup('<article><a href="/a">One</a><time datetime="2026-09-07">7 September</time></article>', "html.parser")
        self.assertEqual(source_discovery.extract_date_near_anchor(soup.a).day, 7)

    def test_corrupt_or_wrong_shape_ledger_fails_closed(self):
        path = Path(config.SEEN_LEDGER_PATH)
        path.parent.mkdir(parents=True)
        for value in ("{broken", "[]", '{"items": []}', '{"items": {"bad": {}}}'):
            path.write_text(value)
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                seen_ledger.load_ledger(str(self.root))
            self.assertEqual(path.read_text(), value)

    def test_atomic_replace_failure_preserves_previous_state(self):
        self.annotate(publication())
        path = Path(config.SEEN_LEDGER_PATH)
        before = path.read_bytes()
        original_replace = state_storage.os.replace
        def fail_primary(source, destination):
            if os.path.abspath(destination) == str(path):
                raise OSError("simulated interrupted replacement")
            return original_replace(source, destination)
        with patch.object(state_storage.os, "replace", side_effect=fail_primary):
            with self.assertRaises(OSError):
                self.annotate(publication(url="https://example.org/reports/another"))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(Path(str(path) + ".bak").read_text()), json.loads(before))
        self.assertEqual(list(path.parent.glob(".scanner-*.tmp")), [])

    def test_missing_primary_with_backup_fails_closed(self):
        self.annotate(publication())
        Path(config.SEEN_LEDGER_PATH).unlink()
        with self.assertRaises(RuntimeError):
            seen_ledger.load_ledger(str(self.root))

    def test_other_process_cannot_overlap_scan(self):
        code = 'import config, state_storage; config.SEEN_LEDGER_PATH = ' + repr(config.SEEN_LEDGER_PATH) + ';\nwith state_storage.scanner_lock(): pass'
        with state_storage.scanner_lock():
            result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another scan", result.stderr)
        with state_storage.scanner_lock():
            pass

    def test_url_normalization_preserves_article_ids(self):
        normalize = seen_ledger.normalize_url
        self.assertEqual(normalize("https://EXAMPLE.org:443/a?b=2&a=1&utm_source=x#top"), normalize("https://example.org/a?a=1&b=2"))
        self.assertNotEqual(normalize("https://example.org/?p=1"), normalize("https://example.org/?p=2"))
        self.assertNotEqual(normalize("https://example.org/a?k=1&k=2"), normalize("https://example.org/a?k=2&k=1"))
        self.assertNotEqual(normalize("http://example.org/a"), normalize("https://example.org/a"))
        self.assertEqual(normalize("https://example.org/%7Epaper"), normalize("https://example.org/~paper"))

    def test_observed_redirect_alias_preserves_report_history(self):
        first = self.annotate(publication(url="http://example.org/old"))
        self.report(first)
        redirected = self.annotate(publication(url="http://example.org/old", resolved_url="https://example.org/new", canonical_url="https://example.org/new"), "2026-09-09")
        self.assertEqual(redirected["last_reported_run_date"], "2026-09-08")
        direct = self.annotate(publication(url="https://example.org/new"), "2026-09-10")
        self.assertEqual(direct["last_reported_run_date"], "2026-09-08")

    def test_unrelated_cross_host_canonical_is_not_trusted(self):
        self.assertEqual(content_extractor.resolve_canonical_url("https://unrelated.org/a", "https://example.org/a"), "https://example.org/a")
        self.assertEqual(content_extractor.resolve_canonical_url("https://example.org/", "https://example.org/a"), "https://example.org/a")

    def test_legacy_key_migration_preserves_report_history(self):
        value = seen_ledger.empty_ledger()
        entry = seen_ledger.new_entry({}, "url", "https://example.org/a?b=2&a=1", "2026-09-01")
        entry["last_reported_run_date"] = "2026-09-01"
        value["version"] = 2
        value["items"]["url:legacy-key"] = entry
        seen_ledger.save_ledger(str(self.root), value)
        item = self.annotate(publication(url="https://example.org/a?a=1&b=2"))
        self.assertEqual(item["last_reported_run_date"], "2026-09-01")

    def test_analyzer_cannot_publish_unverified_or_old_items(self):
        for item in (publication(last_reported_run_date="2026-09-07"), publication(date_status="date_unknown", date_source="")):
            data = {"reports": [], "podcasts": [], "events": []}
            analyzer.add_item_to_category(data, item, "report")
            self.assertEqual(data["reports"], [])
            self.assertEqual(len(data["needs_review"]), 1)

    def test_complete_pipeline_reports_once_with_comparison_models(self):
        item = {
            "title": "New AI governance research publication",
            "institution": "Example Institute",
            "url": "https://example.org/reports/ai-governance",
            "published_at": "2026-09-07T10:00:00+10:00",
            "date_source": "rss_published",
            "date": "07 September 2026",
            "discovery_methods": ["rss"],
            "summary": "New research about artificial intelligence and economic security.",
            "author": "Example Author",
            "tags": [],
        }
        args = ["main.py", "--date", "2026-09-08", "--model", "test/one,test/two", "--output-dir", str(self.root / "reports"), "--no-email"]
        def discover(_date):
            return [copy.deepcopy(item)], {"Example Institute": "checked/found 1"}, {}
        with patch.object(sys, "argv", args), patch.object(main, "stage_discover", side_effect=discover), patch.object(config, "OPENROUTER_API_KEY", "test"), patch.object(content_extractor, "extract_page", side_effect=lambda *_: {"extracted_text": "Artificial intelligence governance and economic security research. " * 20, "extracted_date_dt": None}), patch.object(analyzer.ai_client, "generate_json_with_retry", return_value={"analyses": [{"temp_id": 0, "is_material_match": True, "evidence": ["AI governance research"], "category": "report"}]}), patch.object(main.emailer, "send_report_email") as email, contextlib.redirect_stdout(io.StringIO()):
            main.main()
            first_dir = Path(json.loads((self.root / "reports" / "latest.json").read_text())["directory"])
            for model in ("test-one", "test-two"):
                result = json.loads((first_dir / f"report_2026-09-08_{model}.json").read_text(encoding="utf-8"))
                self.assertEqual(len(result["reports"]), 1)
            main.main()
            second_dir = Path(json.loads((self.root / "reports" / "latest.json").read_text())["directory"])
            self.assertNotEqual(first_dir, second_dir)
            self.assertTrue((first_dir / "run.json").exists())
            result = json.loads((second_dir / "report_2026-09-08.json").read_text(encoding="utf-8"))
            self.assertEqual(result["reports"], [])
            email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
