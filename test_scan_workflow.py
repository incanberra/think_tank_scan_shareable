"""Offline acceptance checks for durable runs, recovery and evidence review."""
import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, Mock
from bs4 import BeautifulSoup
import analyzer, audit_logger, config, content_extractor, evidence_selection, http_client
import publication_dates, scan_runtime, topic_utils
from test_scanner_reliability import publication

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('SEEN_LEDGER_PATH',str(self.root/'state'/'seen.json')),('ENRICHMENT_CACHE_DIR',str(self.root/'cache'))]:
            p = patch.object(config,name,value); p.start(); self.addCleanup(p.stop)
    def run_state(self, **kw):
        return scan_runtime.ScanRun(self.root/'reports','2026-09-09',**kw)
    def test_visible_publisher_dates(self):
        fixtures = [
            ('csis.org','<meta name="citation_publication_date" content="Tue, 09/08/2026 - 12:00">','2026-09-08'),
            ('rusi.org','<b aria-label="published date">3 September 2026</b>','2026-09-03'),
            ('cnas.org','<article><h1>Research</h1><p class="sans-serif fz11 bold uppercase">September 04, 2026</p></article>','2026-09-04'),
            ('sipri.org','<article><h1>Essay</h1><time datetime="2026-07-02T12:00:00Z">2 July 2026</time></article>','2026-07-02')]
        for host, html, expected in fixtures:
            date, reason, raw = publication_dates.extract(BeautifulSoup(html,'html.parser'),'https://'+host+'/research/item',content_extractor.parse_date)
            self.assertEqual(date.date().isoformat(),expected); self.assertTrue(reason)
    def test_ambiguous_and_modified_dates_are_not_publications(self):
        for html in ['<article><h1>Index</h1><time datetime="2026-09-01"></time><time datetime="2026-09-02"></time></article>', '<article><h1>Research</h1><time itemprop="dateModified" datetime="2026-09-01"></time></article>']:
            self.assertIsNone(publication_dates.extract(BeautifulSoup(html,'html.parser'),'https://sipri.org/x',content_extractor.parse_date)[0])
    def test_pending_survives_and_actual_retries_are_bounded(self):
        run = self.run_state()
        items = [publication(url=f'https://example.org/research/{i}') for i in range(9)]
        for item in items: run.remember_pending(item,'publication_date_unverified')
        run.persist()
        next_run=self.run_state()
        self.assertTrue(all(i.get('pending_waiting') for i in next_run.prepare_candidates(items)))
        for entry in next_run.state['pending'].values(): entry['next_attempt_at']=(next_run.started-timedelta(days=1)).isoformat()
        selected=next_run.prepare_candidates(items)
        self.assertEqual(sum(bool(i.get('force_refresh')) for i in selected),config.PENDING_RETRIES_PER_SOURCE)
        self.assertEqual(len(next_run.state['pending']),9)
    def test_pending_attempts_pause_and_manual_retry_resets(self):
        item=publication();run=self.run_state()
        for _ in range(config.PENDING_MAX_ATTEMPTS):
            run=self.run_state();run.remember_pending(item,'insufficient_evidence');run.persist()
        self.assertEqual(next(iter(run.state['pending'].values()))['status'],'paused')
        retry=self.run_state(retry_pending=True)
        self.assertEqual(len(retry.pending_items()),1)
    def test_duplicate_filter_precedes_cap_and_backlog_drains(self):
        run=self.run_state(); items=[publication(url=f'https://example.org/research/{i}') for i in range(5)]
        with patch.object(run,'already_reported',side_effect=lambda i:i['url'].endswith('/0')),patch.object(config,'MAX_NATIVE_CANDIDATES_PER_SOURCE',2):
            selected,deferred=run.cap_native(items)
            self.assertEqual(len(selected),2);self.assertEqual(deferred,2)
            for item in selected: item.update(review_selected=False,review_selection_reason='verified_out_of_window')
            run.record_selection(selected)
            self.assertEqual(len(run.state['backlog']),2)
            remaining,_=run.cap_native(items[:1])
            self.assertTrue(set(i['url'] for i in selected).isdisjoint(i['url'] for i in remaining))
    def test_source_checkpoint_closes_gap_but_failure_does_not_advance(self):
        run=self.run_state();old=(run.cutoff-timedelta(days=5)).isoformat()
        run.state['checkpoints']={'healthy':old,'broken':old}
        self.assertEqual(run.window('healthy')[0],run.cutoff-timedelta(days=5,hours=config.CHECKPOINT_OVERLAP_HOURS))
        run.status_notes={'healthy':'checked/no recent items','broken':'sitemap failures: 2'};run.finish(False)
        self.assertEqual(run.state['checkpoints']['healthy'],run.cutoff.isoformat())
        self.assertEqual(run.state['checkpoints']['broken'],old)
    def test_failed_run_has_manifest_and_preserves_latest(self):
        complete=self.run_state();complete.finish(False)
        failed=self.run_state()
        with self.assertRaisesRegex(RuntimeError,'simulated'):
            with scan_runtime.activate(failed): raise RuntimeError('simulated')
        self.assertEqual(json.loads((failed.output_dir/'run.json').read_text())['status'],'failed')
        self.assertEqual(json.loads((complete.root/'latest.json').read_text())['run_id'],complete.run_id)
    def test_cache_invalidation_and_reprocess(self):
        run=self.run_state();item=publication();decision={'is_material_match':False}
        run.cache_decision(item,'model',decision)
        self.assertEqual(run.get_decision(item,'model'),decision)
        self.assertIsNone(run.get_decision(dict(item,extracted_text='changed'),'model'))
        self.assertIsNone(run.get_decision(item,'other-model'))
        with patch.object(topic_utils,'build_topic_prompt_block',return_value='changed ontology'):
            self.assertIsNone(run.get_decision(item,'model'))
        run.reprocess=True;self.assertIsNone(run.get_decision(item,'model'))
    def test_reordered_model_results_and_cache_avoid_repeat_calls(self):
        run=self.run_state();items=[publication(url=f'https://example.org/research/{i}',title=f'Research study {i}',extracted_text='Evidence '*100) for i in range(2)]
        response={'analyses':[{'temp_id':1,'is_material_match':False,'exclusion_reason':'second reason'},{'temp_id':0,'is_material_match':False,'exclusion_reason':'first reason'}]}
        with scan_runtime.activate(run),patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(analyzer.ai_client,'generate_json_with_retry',return_value=response) as call:
            result=analyzer.analyze_items(copy.deepcopy(items),'test-model')
            self.assertEqual([i['exclusion_reason'] for i in result['excluded']],['first reason','second reason'])
            again=analyzer.analyze_items(copy.deepcopy(items),'test-model')
            self.assertEqual(call.call_count,1);self.assertEqual(again['analysis_metrics']['cache_hits'],2)
    def test_missing_duplicate_ids_go_to_pending(self):
        row={'temp_id':0,'is_material_match':False}
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(analyzer.ai_client,'generate_json_with_retry',return_value={'analyses':[row,row]}):
            result=analyzer.analyze_items([publication()],'test-model')
            self.assertEqual(len(result['needs_review']),1);self.assertEqual(result['excluded'],[])
    def test_thin_evidence_never_reaches_model(self):
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(analyzer.ai_client,'generate_json_with_retry') as call:
            result=analyzer.analyze_items([publication(evidence_quality='insufficient')],'test-model')
            call.assert_not_called();self.assertEqual(len(result['needs_review']),1)
    def test_conclusion_and_ownership_evidence_reach_packet(self):
        text='Opening. '*900+' Acquisition and ownership of the critical supply chain. '+'Body. '*1800+'CONCLUSION: foreign dependency.'
        packet=evidence_selection.excerpt(text,6500)
        self.assertLessEqual(len(packet),6500);self.assertIn('Acquisition and ownership',packet);self.assertIn('CONCLUSION',packet)
    def test_transient_http_retries_but_forbidden_does_not(self):
        with patch.object(http_client.requests,'get',side_effect=[Mock(status_code=503),Mock(status_code=200)]) as call,patch.object(http_client.time,'sleep'):
            self.assertEqual(http_client.get('https://example.org').status_code,200);self.assertEqual(call.call_count,2)
        with patch.object(http_client.requests,'get',return_value=Mock(status_code=403)) as call:
            self.assertEqual(http_client.get('https://example.org').status_code,403);self.assertEqual(call.call_count,1)
    def test_source_audit_matches_explicit_selection(self):
        items=[publication(review_selected=i<18,review_selection_reason='unknown_new_reason') for i in range(240)]
        health=audit_logger.build_source_health(items,items,{'Example Institute':'checked/found 240'},{})
        self.assertEqual(sum(r['selected_for_review'] for r in health['sources']),18)
        self.assertEqual(sum(r['skipped_before_review'] for r in health['sources']),222)
    def test_thin_article_can_use_public_pdf_without_replacing_article_date(self):
        url='https://example.org/research/paper'
        response=Mock(status_code=200,url=url,content=b'<html>',headers={'content-type':'text/html'},text='<html><article><h1>Paper</h1><a href="/paper.pdf">Download</a></article></html>')
        pdf=Mock(status_code=200,url='https://example.org/paper.pdf',headers={'content-type':'application/pdf'})
        with patch.object(content_extractor.http_client,'get',side_effect=[response,pdf]),patch.object(content_extractor,'extract_pdf_from_response',return_value={'extracted_text':'Useful research evidence. '*100,'extracted_date_dt':content_extractor.parse_date('2026-09-09')}):
            result=content_extractor.extract_page(url)
        self.assertGreater(len(result['extracted_text']),300);self.assertIsNone(result['extracted_date_dt']);self.assertEqual(result['canonical_url'],url)

if __name__=='__main__':unittest.main()
