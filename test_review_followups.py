import json
import smtplib
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import config
import editorial_report
import emailer
import main
import scan_runtime
import seen_ledger
import source_discovery


class ReviewFollowupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        mock = patch.object(config, "SEEN_LEDGER_PATH", str(self.root / "seen.json"))
        mock.start()
        self.addCleanup(mock.stop)

    def test_queue_keeps_room_for_recent_links_and_drains_older_work(self):
        run = scan_runtime.ScanRun(self.root, "2026-11-20")
        old = [{"url": f"https://example.org/reports/{n}", "institution": "Example"} for n in range(4)]
        fresh = {"url": "https://example.org/reports/new", "institution": "Example",
                 "published_at": run.cutoff - timedelta(hours=3)}
        for item in old:
            run.state["backlog"][scan_runtime.item_key(item)] = {"item": item, "deferred_at": "2026-10-30"}
        with patch.object(config, "MAX_NATIVE_CANDIDATES_PER_SOURCE", 2):
            chosen, deferred = run.cap_native([fresh])
        self.assertEqual([item["url"] for item in chosen], [fresh["url"], old[0]["url"]])
        self.assertEqual(deferred, 3)

    def test_optional_404_does_not_spoil_working_index_or_repeat_probe(self):
        run = scan_runtime.ScanRun(self.root, "2026-11-20")
        source = {"name": "Example", "domain": "example.org", "base_url": "https://example.org/"}
        with scan_runtime.activate(run), patch.object(source_discovery, "fetch_text", return_value=(None, "HTTP 404")) as get:
            _, first, _ = source_discovery.scan_sitemaps(source, run.run_date)
            first_calls = get.call_count
            _, second, _ = source_discovery.scan_sitemaps(source, run.run_date)
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, first_calls + 1)  # robots.txt is checked again
        self.assertEqual(scan_runtime.source_state(f"checked/native found 1 ({second}; checked/index found 1)"), "available")

    def test_stale_backlog_requires_an_agreed_verified_date_outside_the_source_window(self):
        run = scan_runtime.ScanRun(self.root, "2026-11-20")
        item = {"url": "https://example.org/reports/old", "institution": "Example"}
        key = scan_runtime.item_key(item)
        identity = seen_ledger.item_identity(item)
        entry = seen_ledger.new_entry(item, *identity, run.run_date)
        past = (run.cutoff - timedelta(days=6)).isoformat()
        entry.update(first_verified_publication_date=past, last_verified_publication_date=past)
        run.ledger["items"][key] = entry
        run.state["backlog"][key] = {"item": item, "deferred_at": past}
        # A checkpoint gap gives this older publication another chance.
        run.state["checkpoints"]["Example"] = past
        self.assertEqual(len(run.cap_native([item])[0]), 1)
        run.state["checkpoints"]["Example"] = run.cutoff.isoformat()
        self.assertEqual(run.cap_native([item])[0], [])
        self.assertNotIn(key, run.state["backlog"])

    def test_grouping_keeps_related_links_and_leaves_distinct_articles_visible(self):
        reports = [
            {"title": "Iran Update: Strait of Hormuz Shipping and Energy Market Disruption",
             "url": "https://understandingwar.org/iran-update-a", "date": "01 November 2026"},
            {"title": "Iran Update: Strikes on Shipping in Strait of Hormuz Disrupt Oil Flows",
             "url": "https://understandingwar.org/iran-update-b", "date": "02 November 2026"},
            {"title": "Iran Update: Semiconductor supply restrictions",
             "url": "https://understandingwar.org/iran-update-c", "date": "02 November 2026"},
        ]
        for report in reports:
            report.update(institution="Institute for the Study of War (ISW)", tags=[])
        data = {"reports": reports}
        context = editorial_report.context(data, "2026-11-03")
        self.assertEqual(len(context["items"]), 2)
        self.assertEqual(context["items"][0]["supporting_publications"][0]["url"], reports[1]["url"])
        html = editorial_report.generate_html(data, "2026-11-03", {})
        for report in reports:
            self.assertIn(report["url"], html)
        self.assertEqual(len(data["reports"]), 3)

    def test_retry_sends_only_refused_recipient_and_keeps_message_id(self):
        html, pdf = self.root / "report.html", self.root / "report.pdf"
        html.write_text("<h1>Edition</h1>", encoding="utf-8")
        pdf.write_bytes(b"saved attachment")
        first, second = Mock(), Mock()
        first.sendmail.return_value = {"two@example.org": (450, b"Try later")}
        second.sendmail.return_value = {}
        first.__enter__ = Mock(return_value=first)
        first.__exit__ = Mock(return_value=False)
        second.__enter__ = Mock(return_value=second)
        second.__exit__ = Mock(return_value=False)
        with patch.object(config, "SMTP_SENDER_EMAIL", "scanner@example.org"), \
                patch.object(config, "SMTP_SENDER_PASSWORD", "test"), \
                patch.object(config, "SMTP_RECEIVER_EMAIL", "one@example.org,two@example.org"), \
                patch.object(emailer.smtplib, "SMTP", side_effect=[first, second]) as smtp:
            emailer.queue_saved_email(self.root, "2026-11-20", "Edition", html, pdf, {})
            manifest = self.root / "run.json"
            manifest.write_text('{"status":"failed"}', encoding="utf-8")
            self.assertFalse(emailer.deliver_saved_email(self.root))
            main.retry_edition_email(self.root)
            self.assertTrue(json.loads(manifest.read_text(encoding="utf-8"))["email_sent"])
            self.assertTrue(emailer.deliver_saved_email(self.root))
        record = json.loads((self.root / emailer.DELIVERY_FILE).read_text(encoding="utf-8"))
        self.assertEqual(second.sendmail.call_args.args[1], ["two@example.org"])
        self.assertIn(record["message_id"], second.sendmail.call_args.args[2])
        self.assertEqual(smtp.call_count, 2)
        self.assertEqual(smtp.call_args.kwargs["timeout"], emailer.SMTP_TIMEOUT)

    def test_disconnect_during_send_is_never_retried_implicitly(self):
        html, pdf = self.root / "report.html", self.root / "report.pdf"
        html.write_text("Edition", encoding="utf-8")
        pdf.write_bytes(b"saved attachment")
        server = Mock()
        server.__enter__ = Mock(return_value=server)
        server.__exit__ = Mock(return_value=False)
        server.sendmail.side_effect = smtplib.SMTPServerDisconnected
        with patch.object(config, "SMTP_SENDER_EMAIL", "scanner@example.org"), \
                patch.object(config, "SMTP_SENDER_PASSWORD", "test"), \
                patch.object(config, "SMTP_RECEIVER_EMAIL", "one@example.org"), \
                patch.object(emailer.smtplib, "SMTP", return_value=server) as smtp:
            emailer.queue_saved_email(self.root, "2026-11-20", "Edition", html, pdf, {})
            self.assertFalse(emailer.deliver_saved_email(self.root))
            self.assertFalse(emailer.deliver_saved_email(self.root))
        smtp.assert_called_once()
        record = json.loads((self.root / emailer.DELIVERY_FILE).read_text(encoding="utf-8"))
        self.assertEqual(record["recipients"]["one@example.org"], "uncertain")
