"""Acceptance checks for grounded tags, tiers, dates and failed-review recovery."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from bs4 import BeautifulSoup
import ai_client, analyzer, audit_logger, config, content_extractor, ddg_searcher
import editorial_policy, editorial_report, evidence_selection, publication_dates
import report_generator, scan_runtime, source_discovery
from test_scanner_reliability import publication

BODY = 'Export controls restrict access to advanced chips and constrain strategic technology supply chains.'
TOPIC = 'Economic Coercion'

def positive_decision(index=0, role='central'):
    return {'temp_id': index, 'needs_review': False, 'is_material_match': True,
        'title': 'Chip export restrictions', 'author': 'Example author',
        'matched_topics': [TOPIC], 'summary': BODY, 'why_it_matters': BODY,
        'importance_score': 3, 'category': 'report', 'event_time': None,
        'relevance_confidence': 'high', 'evidence': [BODY], 'exclusion_reason': '',
        'date_note': '', 'editorial_tier': 'main' if role == 'central' else 'further_reading',
        'coverage_relationship': 'central' if role == 'central' else 'sustained_support',
        'topic_evidence': [{'topic': TOPIC, 'role': role, 'mechanism': 'Export restrictions constrain strategic chip access.', 'quotes': [BODY]}]}

def negative_decision(index=0, reason='No material economic-security analysis'):
    row=positive_decision(index)
    row.update(is_material_match=False, matched_topics=[], topic_evidence=[], evidence=[],
               editorial_tier='excluded', exclusion_reason=reason, coverage_relationship='none')
    return row

class ReviewImprovements(unittest.TestCase):
    def item(self, **kw):
        return publication(extracted_text=BODY * 20, evidence_quality='sufficient', **kw)

    def test_each_tag_requires_its_own_evidence(self):
        row=positive_decision();row['matched_topics'].append('Critical Minerals')
        self.assertIn('every topic', editorial_policy.validation_error(self.item(), row))

    def test_fabricated_quote_rejected(self):
        row=positive_decision();row['topic_evidence'][0]['quotes']=['A fabricated statement about sanctions and foreign dependency.']
        self.assertIn('quote absent',editorial_policy.validation_error(self.item(),row))

    def test_title_and_summary_cannot_support_a_topic(self):
        self.assertIn('quote absent',editorial_policy.validation_error(publication(extracted_text='Unrelated body.',summary=BODY,title=BODY),positive_decision()))

    def test_invalid_topic_cannot_fall_back_to_keyword_hint(self):
        row=positive_decision();row['matched_topics']=['made up topic']
        self.assertIn('invalid decision schema', editorial_policy.validation_error(self.item(),row))

    def test_supporting_topic_requires_further_reading(self):
        row=positive_decision(role='supporting')
        self.assertEqual(editorial_policy.validation_error(self.item(),row),'')
        row['editorial_tier']='main'
        self.assertIn('contradicts',editorial_policy.validation_error(self.item(),row))

    def test_keywords_not_sent_as_model_labels(self):
        packet=analyzer.compact_item_for_prompt(self.item(topic_hints=[{'topic':'Critical Minerals'}]),0)
        self.assertNotIn('topic_hints',packet)

    def test_failed_batch_recovers_individual_items(self):
        items=[self.item(url=f'https://example.org/report/{i}',title=f'Specific research paper number {i}') for i in range(2)]
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch('scan_runtime.current',return_value=None),patch.object(ai_client,'generate_json_with_retry',side_effect=[ValueError('empty batch'),{'analyses':[positive_decision()]},{'analyses':[positive_decision()]}]) as call:
            result=analyzer.analyze_items(items,'test')
        self.assertEqual(len(result['reports']),2);self.assertEqual(call.call_count,3)
        self.assertEqual(result['analysis_metrics']['individual_recoveries'],2)

    def test_invalid_decision_repaired_once(self):
        bad=positive_decision();bad['topic_evidence']=[]
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch('scan_runtime.current',return_value=None),patch.object(ai_client,'generate_json_with_retry',side_effect=[{'analyses':[bad]},{'analyses':[positive_decision()]}]) as call:
            result=analyzer.analyze_items([self.item()],'test')
        self.assertEqual(len(result['reports']),1);self.assertEqual(call.call_count,2)
        self.assertEqual(result['validation_failures'][0]['stage'],'initial')

    def test_repeated_invalid_decision_held_and_not_cached(self):
        bad=positive_decision();bad['topic_evidence']=[]
        with tempfile.TemporaryDirectory() as temp:
            run=scan_runtime.ScanRun(Path(temp)/'reports','2026-09-08')
            with patch.object(config,'OPENROUTER_API_KEY','test'),patch('scan_runtime.current',return_value=run),patch.object(ai_client,'generate_json_with_retry',return_value={'analyses':[bad]}) as call:
                result=analyzer.analyze_items([self.item()],'test')
            self.assertEqual(len(result['needs_review']),1);self.assertEqual(call.call_count,2)
            self.assertIsNone(run.get_decision(self.item(),'test'))

    def test_provider_quarantine_used_for_retry_and_next_request(self):
        def response(content,provider):
            r=Mock(status_code=200);r.json.return_value={'provider':provider,'choices':[{'finish_reason':'stop','message':{'content':content}}]};return r
        run=Mock(model_requests=[],failed_review_providers=set())
        payloads=[]
        responses=iter([response('','Z.AI'),response('{}','Together'),response('{}','Together')])
        def post(*a,**kw):payloads.append(copy.deepcopy(kw['json']));return next(responses)
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch('scan_runtime.current',return_value=run),patch.object(ai_client.requests,'post',side_effect=post),patch.object(ai_client.time,'sleep'):
            ai_client.generate_json_with_retry('test',[],response_schema=editorial_policy.response_schema())
            ai_client.generate_json_with_retry('test',[])
        self.assertEqual(payloads[1]['provider']['ignore'],['z-ai'])
        self.assertEqual(payloads[2]['provider']['ignore'],['z-ai'])
        self.assertEqual(payloads[0]['response_format']['type'],'json_schema')

    def test_provider_specific_rate_limit_changes_route(self):
        failed=Mock(status_code=429);failed.json.return_value={'error':{'metadata':{'provider_name':'Together','limit_source':'upstream_provider_shared_pool'}}}
        good=Mock(status_code=200);good.json.return_value={'provider':'Fireworks','choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
        payloads=[];responses=iter([failed,good])
        def post(*a,**kw):payloads.append(copy.deepcopy(kw['json']));return next(responses)
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch('scan_runtime.current',return_value=None),patch.object(ai_client.requests,'post',side_effect=post),patch.object(ai_client.time,'sleep'):
            ai_client.generate_json_with_retry('test',[])
        self.assertEqual(payloads[1]['provider']['ignore'],['together'])

    def test_bibliography_does_not_replace_ending(self):
        text=BODY*100+'\nConclusion: dependency and diversification deserve attention.\n\nReferences\n'+('Unrelated sanctions citation.\n'*300)
        packet=evidence_selection.excerpt(text,6500)
        self.assertIn('Conclusion: dependency',packet);self.assertNotIn('Unrelated sanctions citation',packet)

    def test_fpri_date_from_current_article_metadata(self):
        soup=BeautifulSoup('<h1>Article</h1><ul class="meta-desc-article"><li><span>October 1, 2026</span></li></ul><aside><time datetime="2026-10-05">Related</time></aside>','html.parser')
        date,detail,_=publication_dates.extract(soup,'https://www.fpri.org/article/test',content_extractor.parse_date)
        self.assertEqual(date.strftime('%Y-%m-%d'),'2026-10-01');self.assertEqual(detail,'fpri_visible_publication_date')

    def test_rand_publication_label_never_access_date_or_year(self):
        for text,expected in [('Research Published Oct 2, 2026','2026-10-02'),('Research Published 1993',None)]:
            soup=BeautifulSoup(f'<main><h1>Paper</h1><p>{text}</p><p>As of October 5, 2026</p></main>','html.parser')
            date,_,_=publication_dates.extract(soup,'https://www.rand.org/pubs/reports/paper.html',content_extractor.parse_date)
            self.assertEqual(date.strftime('%Y-%m-%d') if date else None,expected)

    def test_ambiguous_card_date_held(self):
        soup=BeautifulSoup('<article><a href="/a">Article</a><time datetime="2026-10-01"></time><time datetime="2026-10-02"></time></article>','html.parser')
        self.assertIsNone(source_discovery.extract_date_near_anchor(soup.a))

    def test_rusi_visible_time_without_datetime(self):
        soup=BeautifulSoup('<article><h1>Article</h1><time>27 November 2025</time></article>','html.parser')
        date,_,_=publication_dates.extract(soup,'https://rusi.org/article',content_extractor.parse_date)
        self.assertEqual(date.strftime('%Y-%m-%d'),'2025-11-27')

    def test_hudson_event_is_not_a_publication_date(self):
        soup=BeautifulSoup('<div class="hero__event-date"><div class="block-field-block-node-event-detail-field-start-datetime">October 22 - 25, 2026</div></div><h1>Event</h1>','html.parser')
        start,end,_=publication_dates.event_dates(soup,'https://hudson.org/events/test',content_extractor.parse_date)
        self.assertEqual(start.day,22);self.assertEqual(end.day,25)
        self.assertIsNone(publication_dates.extract(soup,'https://hudson.org/events/test',content_extractor.parse_date)[0])

    def test_category_pages_removed(self):
        for path in ('/event-summaries','/events/open-to-all','/explore-our-research/publications/cyber-effects-perspectives'):
            self.assertTrue(source_discovery.native_rejection_reason('https://rusi.org'+path))

    def test_rand_feed_does_not_remove_native_coverage(self):
        with patch.object(source_discovery,'discover_native_sources',return_value=([],{},{})) as call:
            source_discovery.discover_search_only_sources('2026-10-05')
        self.assertIn('RAND Corporation',call.call_args.kwargs['source_names'])

    def test_invalid_numeric_boolean_is_rejected_locally(self):
        row=positive_decision();row['needs_review']=0
        self.assertIn('invalid decision',editorial_policy.validation_error(self.item(),row))

    def test_sustained_support_cannot_be_excluded(self):
        row=negative_decision();row['coverage_relationship']='sustained_support'
        self.assertIn('cannot be excluded',editorial_policy.validation_error(self.item(),row))

    def test_section_and_article_evidence_derived_from_validated_topics(self):
        row=positive_decision(role='supporting');row.update(editorial_tier='main',coverage_relationship='central',evidence=['A paraphrased claim'])
        prepared=editorial_policy.derive_editorial_fields(row)
        self.assertEqual(prepared['editorial_tier'],'further_reading')
        self.assertEqual(prepared['evidence'],[BODY])
        self.assertEqual(editorial_policy.validation_error(self.item(),prepared),'')
        self.assertEqual(row['editorial_tier'],'main')

    def test_bad_quote_discarded_only_if_topic_has_verified_evidence(self):
        row=positive_decision();row['topic_evidence'][0]['quotes'].append('An invented quote about minerals and sanctions.')
        prepared=editorial_policy.derive_editorial_fields(row,self.item())
        self.assertEqual(prepared['topic_evidence'][0]['quotes'],[BODY])
        self.assertEqual(editorial_policy.validation_error(self.item(),prepared),'')
        row['topic_evidence'][0]['quotes']=['An invented quote about minerals and sanctions.']
        self.assertNotEqual(editorial_policy.validation_error(self.item(),editorial_policy.derive_editorial_fields(row,self.item())),'')

    def test_trusted_body_survives_date_adapter_refresh_denial(self):
        entry={'schema_version':8,'cache_saved_at':'2099-01-01T00:00:00+00:00',
               'etag':'old','page_data':{'extracted_text':BODY,'extraction_status':'ok'}}
        self.assertFalse(content_extractor.cache_entry_is_fresh(entry))
        with patch.object(content_extractor,'load_cache_entry',return_value=entry),patch.object(content_extractor.http_client,'get',return_value=Mock(status_code=403,headers={})) as call:
            data=content_extractor.extract_page('https://example.org/report')
        self.assertEqual(data['extracted_text'],BODY)
        self.assertEqual(data['cache_status'],'stale_fallback_http_403')
        self.assertNotIn('If-None-Match',call.call_args.kwargs['headers'])

    def test_auth_failure_is_not_repeated_as_individual_requests(self):
        items=[self.item(url=f'https://example.org/report/{i}',title=f'Specific study number {i}') for i in range(2)]
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch('scan_runtime.current',return_value=None),patch.object(ai_client,'generate_json_with_retry',side_effect=RuntimeError('OpenRouter HTTP 401')) as call:
            result=analyzer.analyze_items(items,'test')
        self.assertEqual(call.call_count,1);self.assertEqual(len(result['needs_review']),2)

    def test_legacy_no_api_entrypoint_never_publishes(self):
        data=analyzer.analyze_without_api([self.item()])
        self.assertEqual(data['reports'],[]);self.assertEqual(len(data['needs_review']),1)

    def test_deferred_dates_distinguished_from_fetched_unknown(self):
        a=self.item(date_status='date_unknown',extraction_status='ok');b=dict(a,pending_waiting=True,extraction_status='not_enriched')
        health=audit_logger.build_source_health([a,b],[a,b],{},{});row=next(r for r in health['sources'] if r['source']=='Example Institute')
        self.assertEqual(row['date_unknown'],2);self.assertEqual(row['fetched_date_unknown'],1);self.assertEqual(row['pending_deferred'],1)

    def test_both_sections_render_html_pdf_and_markdown(self):
        main=analyzer.make_final_item(self.item(),positive_decision());further=analyzer.make_final_item(self.item(),positive_decision(role='supporting'))
        data={'reports':[further,main],'podcasts':[],'events':[]}
        html=editorial_report.generate_html(data,'2026-10-05',{})
        self.assertLess(html.index('Main brief'),html.index('Further reading'))
        md=report_generator.generate_markdown(data,'2026-10-05',{})
        self.assertIn('Main brief',md);self.assertIn('Further reading',md)
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'report.pdf');editorial_report.generate_pdf(data,'2026-10-05',{},path)
            from pypdf import PdfReader
            text=''.join(p.extract_text() for p in PdfReader(path).pages)
            self.assertIn('Main brief',text);self.assertIn('Further reading',text)

    def test_search_challenge_is_a_failure(self):
        response=Mock(status_code=200,text='<html>captcha challenge</html>',content=b'<html>captcha challenge</html>')
        with patch.object(ddg_searcher.requests,'post',return_value=response):
            self.assertEqual(ddg_searcher.search_ddg_html('test-query'),[])
        self.assertEqual(ddg_searcher.SEARCH_FAILURES['test-query'],'bot challenge')

if __name__=='__main__':unittest.main()
