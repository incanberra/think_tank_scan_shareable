"""Production regression checks independent of the experimental Jev modules."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from pypdf import PdfReader

import article_scope
import config
import content_extractor
import editorial_report
from topic_badges import PALETTE, TopicPills, badges

BODY = "Strategic mineral imports create exposure to export restrictions. Diversified refining reduces vulnerability. " * 12
ATLANTIC = "https://www.atlanticcouncil.org/article/"


class ExtractionTests(unittest.TestCase):
    def test_related_jsonld_and_teaser_are_not_article_evidence(self):
        graph = [{"@type": "Article", "url": "https://example.org/related", "articleBody": BODY},
                 {"@type": "Article", "url": "https://example.org/current", "description": "Long teaser. " * 40}]
        html = '<link rel="canonical" href="https://example.org/current"><script type="application/ld+json">' + json.dumps(graph) + '</script><article><p>Short current article teaser.</p></article>'
        text = content_extractor.extract_text_from_html(html)
        self.assertNotIn("Strategic mineral", text)
        self.assertNotIn("Long teaser", text)

    def test_current_jsonld_body_is_retained(self):
        html = '<link rel="canonical" href="https://example.org/current"><script type="application/ld+json">' + json.dumps({"@type": "NewsArticle", "@id": "https://example.org/current#article", "articleBody": BODY}) + '</script>'
        self.assertEqual(content_extractor.extract_text_from_html(html), BODY.strip())

    def test_untyped_organization_text_is_not_used_as_article(self):
        soup = BeautifulSoup('<script type="application/ld+json">' + json.dumps({"text": BODY}) + '</script>', 'html.parser')
        self.assertEqual(content_extractor.extract_json_ld_body(soup), '')

    def test_nested_paragraphs_are_counted_once(self):
        opening = "Strategic dependence on imports increases vulnerability to export restrictions."
        ending = "Diversified processing capacity reduces exposure to coercion and disruptions."
        body = content_extractor.extract_text_from_html('<article><p>' + opening + '<p>' + ending + '</p></p></article>')
        self.assertIn(opening, body)
        self.assertEqual(body.count(ending), 1)

    def flight_html(self, url):
        node = ["$", "div", None, {"className": "prose dark:prose-invert", "children": ["$L2"]}]
        paragraph = ["$", "p", None, {"children": [BODY]}]
        chunks = '1:' + json.dumps(node) + '\n2:' + json.dumps(paragraph) + '\n3:' + json.dumps({"menu": "RELATED ARTICLE TEXT"})
        return '<link rel="canonical" href="' + url + '"><main><p>Short teaser.</p></main><script>self.__next_f.push(' + json.dumps([1, chunks]) + ');</script>'

    def test_lowy_flight_body_excludes_teasers_and_related_text(self):
        text = content_extractor.extract_text_from_html(self.flight_html('https://www.lowyinstitute.org/the-interpreter/test'))
        self.assertIn("Strategic mineral", text)
        self.assertNotIn("RELATED ARTICLE", text)
        self.assertNotIn("Short teaser", text)

    def test_lowy_parser_is_restricted_to_publisher_and_template(self):
        for url in ('https://example.org/the-interpreter/test', 'https://www.lowyinstitute.org.evil.example/the-interpreter/test', 'https://www.lowyinstitute.org/publication/test'):
            self.assertNotIn("Strategic mineral", content_extractor.extract_text_from_html(self.flight_html(url)))

    def test_lowy_malformed_data_and_cycles_fail_safely(self):
        html = '<link rel="canonical" href="https://www.lowyinstitute.org/the-interpreter/test"><script>self.__next_f.push([1,"1:\"$L1\"\\n"])</script>'
        self.assertEqual(content_extractor.extract_text_from_html(html), '')
        self.assertEqual(content_extractor.extract_text_from_html(html.replace('[1,', '[invalid,')), '')

    def test_atlantic_tail_boundary_keeps_current_article(self):
        for bio in ('Valbona Zeneli , PhD, is a nonresident senior fellow at the Atlantic Council.',
                    'Jeff Lightfoot is senior director at the Center for International Private Enterprise.'):
            text = BODY + '\n\n' + bio + '\n\nRelated: Sanctions and cyber espionage.'
            cleaned, audit = article_scope.trim(text, ATLANTIC)
            self.assertEqual(cleaned, BODY.rstrip())
            self.assertGreater(audit['removed_chars'], 0)
            for url in ('https://example.org/article/', 'https://www.atlanticcouncil.org.evil.example/article/'):
                self.assertEqual(article_scope.trim(text, url)[0], text)

    def test_biography_cut_does_not_remove_thin_or_majority_body(self):
        for text in ('Short body\n\nJane Smith is a senior fellow at the Atlantic Council.',
                     BODY + '\n\nJane Smith is a senior fellow at the Atlantic Council.\n\n' + BODY * 3,
                     'Europe is a senior partner of the Atlantic Council. ' * 15):
            self.assertEqual(article_scope.trim(text, ATLANTIC)[0], text)

    def test_live_extraction_uses_response_url_for_tail_boundary(self):
        html = '<article><p>' + BODY + '</p><p>Jane Smith is a nonresident senior fellow at the Atlantic Council.</p><p>Related: Sanctions and export-control chokepoints.</p></article>'
        text = content_extractor.extract_text_from_html(html, source_url=ATLANTIC)
        self.assertNotIn('Related:', text)
        self.assertIn('Strategic mineral', text)

    def test_polluted_cache_versions_are_invalidated(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'ENRICHMENT_CACHE_DIR', directory), patch.object(config, 'ENABLE_ENRICHMENT_CACHE', True):
            path = Path(content_extractor.cache_path_for_url(ATLANTIC))
            for version in (4, 5, 6, 7):
                path.write_text(json.dumps({'schema_version': version, 'page_data': {'extracted_text': 'old polluted body'}}))
                self.assertIsNone(content_extractor.load_cache_entry(ATLANTIC))
            content_extractor.write_cache_entry(ATLANTIC, {'extracted_text': BODY})
            self.assertEqual(content_extractor.load_cache_entry(ATLANTIC)['page_data']['extracted_text'], BODY)


class PillTests(unittest.TestCase):
    def test_palette_matches_live_topics(self):
        self.assertEqual(set(PALETTE), set(config.TOPIC_BADGE_CONFIGS))

    def test_shadow_proposals_cannot_add_remove_or_reorder_live_pills(self):
        item = {'tags': ['Critical Minerals', 'Energy Independence and Transition'],
                'topic_classification': {'proposed_tags': ['Economic Coercion'], 'topics': [{'topic': 'Energy Independence and Transition', 'role': 'central', 'material_probability': 1}]},
                'topic_confirmation': {'status': 'success', 'topics': [{'topic': 'Energy Independence and Transition', 'role': 'central'}]}}
        before = copy.deepcopy(item)
        self.assertEqual([p['topic'] for p in badges(item)[0]], item['tags'])
        self.assertEqual(item, before)

    def test_pill_limit_wraps_and_retains_all_audit_tags(self):
        item = {'tags': list(PALETTE) + ['<script>bad</script>', 'Critical Minerals']}
        before = copy.deepcopy(item)
        pills, hidden = badges(item)
        self.assertEqual((len(pills), hidden), (3, 11))
        self.assertEqual(item, before)
        flow = TopicPills(pills, hidden)
        width, height = flow.wrap(200, 800)
        self.assertGreater(height, 23)
        self.assertTrue(all(x + w <= width for _, x, _, w in flow.placements))

    def test_html_and_pdf_have_safe_labels_without_changing_decisions(self):
        item = {'title': '<script>source</script>', 'date': '', 'tags': ['Critical Minerals'], 'institution': 'Example', 'summary': 'Source summary', 'url': 'javascript:bad', 'importance_score': 3, 'relevance_confidence': 'high'}
        data = {'reports': [item]}
        before = copy.deepcopy(data)
        html = editorial_report.generate_html(data, '2026-10-02', {})
        self.assertIn('Critical minerals', html)
        self.assertIn('border-radius:999px', html)
        self.assertNotIn('<script>source</script>', html)
        self.assertNotIn('javascript:bad', html)
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / 'report.pdf'
            editorial_report.generate_pdf(data, '2026-10-02', {}, str(pdf))
            text = ''.join(p.extract_text() for p in PdfReader(pdf).pages)
            self.assertIn('Critical minerals', text)
            self.assertIn('Date unverified', text)
        self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main()
