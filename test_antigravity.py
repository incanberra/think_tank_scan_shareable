"""Offline transport and full-pipeline checks; never contact models or SMTP."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import ai_client
import antigravity_client
import antigravity_preview
import config
import emailer
import main
from test_review_improvements import positive_decision, BODY


def cli_result(data=None, **overrides):
    envelope = {"status": "SUCCESS", "response": "OK", "structured_output": data,
                "conversation_id": "offline-test", "usage": {"total_tokens": 10}, **overrides}
    return subprocess.CompletedProcess([], 0, json.dumps({"event": "result", "result": envelope}) + "\n", "")


class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.provider = patch.object(config, "AI_PROVIDER", "antigravity")
        self.provider.start()
        self.addCleanup(self.provider.stop)

    def test_json_transport_no_openrouter_and_safe_stdin(self):
        schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
        with patch('antigravity_client.subprocess.run', return_value=cli_result({"value": 1})) as cli, patch('ai_client.requests.post') as http, patch('scan_runtime.current', return_value=None):
            result = ai_client.generate_json_with_retry('antigravity/test-model', [{"role": "user", "content": 'x' * 100000}], response_schema=schema)
        self.assertEqual(result, {"value": 1})
        http.assert_not_called()
        command = cli.call_args.args[0]
        self.assertIn('--sandbox', command)
        self.assertIn('test-model', command)
        self.assertFalse(cli.call_args.kwargs['shell'])
        self.assertLess(sum(map(len, command)), 2000)
        self.assertGreater(len(cli.call_args.kwargs['input']), 100000)
        self.assertFalse(Path(cli.call_args.kwargs['cwd']).exists())

    def test_missing_cli_failed_status_and_timeout_fail_closed(self):
        failures = [FileNotFoundError(), subprocess.TimeoutExpired('agy', 1)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch('antigravity_client.subprocess.run', side_effect=failure), patch('scan_runtime.current', return_value=None):
                with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                    antigravity_client.generate('antigravity/default', [])
        for result in [cli_result(status='ERROR'), subprocess.CompletedProcess([], 3, '', ''), subprocess.CompletedProcess([], 0, '', '')]:
            with patch('antigravity_client.subprocess.run', return_value=result), patch('scan_runtime.current', return_value=None):
                with self.assertRaises((RuntimeError, ValueError)):
                    antigravity_client.generate('antigravity/default', [])

    def test_schema_mismatch_rejected(self):
        with patch('antigravity_client.subprocess.run', return_value=cli_result({"value": "wrong"})), patch('scan_runtime.current', return_value=None):
            from jsonschema import ValidationError
            with self.assertRaises(ValidationError):
                antigravity_client.generate('antigravity/default', [], response_schema={"type": "object", "properties": {"value": {"type": "integer"}}})

    def test_email_block_even_with_smtp_credentials(self):
        with patch.object(config, 'SMTP_SENDER_EMAIL', 'test@example.com'), patch.object(config, 'SMTP_SENDER_PASSWORD', 'unused'), patch('emailer.smtplib.SMTP') as smtp, patch('emailer.smtplib.SMTP_SSL') as ssl:
            self.assertFalse(emailer.send_report_email('2026-10-26', 'test', None))
            smtp.assert_not_called()
            ssl.assert_not_called()

    def test_preview_isolates_state_and_forces_no_email(self):
        original = config.SEEN_LEDGER_PATH
        with tempfile.TemporaryDirectory() as root, patch.object(config, 'PROJECT_DIR', root), patch.object(config, 'SEEN_LEDGER_PATH', original), patch.object(config, 'ENRICHMENT_CACHE_DIR', 'unused'), patch.object(sys, 'argv', ['antigravity_preview.py']), patch('antigravity_client.generate', return_value='OK'), patch('main.main') as scanner:
            antigravity_preview.main()
            self.assertIn('--no-email', sys.argv)
            self.assertIn('antigravity-preview', config.SEEN_LEDGER_PATH)
            self.assertNotEqual(config.SEEN_LEDGER_PATH, original)
            scanner.assert_called_once()

    def test_pipeline_creates_reports_with_cli_response_and_no_key_or_email(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'reports'
            item = {"title": "OFFLINE TEST FIXTURE: AI governance research", "institution": "Example Institute",
                    "url": "https://example.org/reports/ai-governance", "published_at": "2026-09-07T10:00:00+10:00",
                    "date_source": "rss_published", "date": "07 September 2026", "discovery_methods": ["rss"],
                    "summary": "Artificial intelligence and economic security.", "author": "Test fixture", "tags": []}
            with patch.object(config, 'SEEN_LEDGER_PATH', str(Path(root) / 'state' / 'seen.json')), patch.object(config, 'ENRICHMENT_CACHE_DIR', str(Path(root) / 'cache')), patch.object(config, 'OPENROUTER_API_KEY', None), patch.object(sys, 'argv', ['main.py', '--date', '2026-09-08', '--model', 'antigravity/default', '--output-dir', str(output)]), patch('main.stage_discover', return_value=([copy.deepcopy(item)], {'Example Institute': 'checked/found 1'}, {})), patch('content_extractor.extract_page', return_value={"extracted_text": BODY * 20, "extracted_date_dt": None}), patch('antigravity_client.subprocess.run', return_value=cli_result({'analyses': [positive_decision()]})) as cli, patch('ai_client.requests.post') as http, patch('emailer.send_report_email') as email, contextlib.redirect_stdout(io.StringIO()):
                main.main()
            directory = Path(json.loads((output / 'latest.json').read_text())['directory'])
            report = json.loads((directory / 'report_2026-09-08.json').read_text())
            self.assertEqual(len(report['reports']), 1)
            self.assertTrue(list(directory.glob('*.pdf')))
            self.assertTrue(list(directory.glob('*.html')))
            manifest = json.loads((directory / 'run.json').read_text())
            self.assertEqual(manifest['model_requests'][0]['provider'], 'antigravity-cli')
            cli.assert_called_once()
            http.assert_not_called()
            email.assert_not_called()


if __name__ == '__main__':
    unittest.main()
