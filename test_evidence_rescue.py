import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import analyzer
import candidate_selector
import config
import evidence_rescue
import scan_runtime
from test_scanner_reliability import publication


class RescueTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for key, value in [('SEEN_LEDGER_PATH', str(self.root / 'seen.json')),
                           ('ENRICHMENT_CACHE_DIR', str(self.root / 'cache')),
                           ('RESCUE_MAX_ITEMS_PER_RUN', 10)]:
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def thin(self, **kwargs):
        return publication(evidence_quality='insufficient', **kwargs)

    def test_cap_source_balance_cache_and_reselection(self):
        original = [self.thin(url=f'https://example.org/reports/{i}') for i in range(12)]
        original.append(self.thin(url='https://other.org/reports/a', institution='Other'))
        with patch.object(evidence_rescue, 'recover', return_value={'text': 'Evidence ' * 100,
                                                                  'evidence_url': 'https://example.org/a.pdf'}) as fetch:
            first = copy.deepcopy(original)
            audit = evidence_rescue.rescue_items(first, str(self.root))
            self.assertEqual(audit['attempted'], 10)
            self.assertEqual(audit['deferred'], 3)
            self.assertEqual(first[-1]['rescue_status'], 'recovered')
            self.assertTrue(candidate_selector.should_review_candidate(first[0])[0])
            second = copy.deepcopy(original)
            cached = evidence_rescue.rescue_items(second, str(self.root))
            self.assertEqual(cached['cache_hits'], 10)
            self.assertEqual(fetch.call_count, 10)

    def test_date_delivery_and_pending_gates(self):
        items = [self.thin(last_reported_run_date='2026-09-07'),
                 self.thin(date_status='verified_out_of_window'), self.thin(pending_waiting=True),
                 self.thin(is_listing_page=True), self.thin(paywall_detected=True)]
        with patch.object(evidence_rescue, 'recover') as fetch:
            self.assertEqual(evidence_rescue.rescue_items(items, str(self.root))['attempted'], 0)
            fetch.assert_not_called()

    def test_explicit_publisher_alternative_and_date_preservation(self):
        response = Mock(status_code=200, url='https://example.org/reports/ai-governance',
                        content=b'<html></html>',
                        headers={'content-type': 'text/html'},
                        text='<html><meta name="citation_pdf_url" content="/paper.pdf"></html>')
        with patch.object(evidence_rescue.http_client, 'get', return_value=response), \
             patch.object(evidence_rescue.extractor, 'extract_page', return_value={
                 'extracted_text': 'Verified evidence ' * 100, 'extracted_date': '1900-01-01'}) as extract:
            item = self.thin()
            evidence_rescue.rescue_items([item], str(self.root))
            extract.assert_called_once_with('https://example.org/paper.pdf')
            self.assertEqual(item['published_at_verified'], '2026-09-07T10:00:00+10:00')
            self.assertEqual(item['rescue_evidence_url'], 'https://example.org/paper.pdf')
        self.assertFalse(evidence_rescue.publisher_url(response.url, 'https://example.org.evil.test/a.pdf'))

    def test_failed_recovery_stays_pending_without_ai(self):
        with patch.object(evidence_rescue, 'recover', return_value={}):
            item = self.thin()
            evidence_rescue.rescue_items([item], str(self.root))
            self.assertEqual(candidate_selector.should_review_candidate(item), (False, 'insufficient_evidence'))

    def test_rescue_ai_budget_and_persistent_decision_cache(self):
        item = publication(extracted_text='Evidence sanctions ' * 5000, evidence_quality='sufficient',
                           rescue_status='recovered', rescue_evidence_url='https://example.org/paper.pdf',
                           summary='S' * 10000)
        packet = analyzer.compact_item_for_prompt(item, 0)
        self.assertLessEqual(len(packet['full_text_excerpt']), 12000)
        self.assertLessEqual(len(packet['source_summary']), 1000)
        run = scan_runtime.ScanRun(self.root / 'reports', '2026-09-08')
        decision = {'analyses': [{'temp_id': 0, 'needs_review': False, 'is_material_match': False,
                                  'exclusion_reason': 'Not materially relevant'}]}
        with patch.object(scan_runtime, 'current', return_value=run), \
             patch.object(config, 'OPENROUTER_API_KEY', 'test'), \
             patch.object(analyzer.ai_client, 'generate_json_with_retry', return_value=decision) as call:
            analyzer.analyze_items([item], 'test-model')
            self.assertEqual(call.call_args.kwargs['max_tokens'], 1600)
            self.assertEqual(call.call_args.kwargs['max_retries'], 2)
            self.assertEqual(call.call_args.kwargs['purpose'], 'evidence_rescue')
        next_run = scan_runtime.ScanRun(self.root / 'reports', '2026-09-08')
        with patch.object(scan_runtime, 'current', return_value=next_run), \
             patch.object(config, 'OPENROUTER_API_KEY', 'test'), \
             patch.object(analyzer.ai_client, 'generate_json_with_retry') as call:
            result = analyzer.analyze_items([item], 'test-model')
            self.assertEqual(result['analysis_metrics']['cache_hits'], 1)
            call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
