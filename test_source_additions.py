import unittest
from unittest.mock import patch
import config, source_discovery
import publication_dates,content_extractor
from bs4 import BeautifulSoup

class NewSourceRoutes(unittest.TestCase):
    def test_new_sources_are_active_and_have_individual_content_routes(self):
        domains={'piie.com','bruegel.org','merics.org','eria.org','iseas.edu.sg'}
        rows=[s for s in config.DISCOVERY_SOURCES if s['domain'] in domains]
        self.assertEqual(len(rows),5)
        for source in rows:
            self.assertIn(source['name'],config.THINK_TANKS)
            self.assertIn(source['name'],config.SEARCH_ONLY_THINK_TANKS)
            self.assertTrue(source['index_paths'])

    def test_detail_routes_and_navigation(self):
        good=[
            'https://www.piie.com/blogs/realtime-economics/2026/strategic-chip-access',
            'https://www.piie.com/publications/working-papers/2026/trade-controls',
            'https://www.bruegel.org/working-paper/industrial-policy',
            'https://merics.org/en/comment/chinas-industrial-capacity',
            'https://merics.org/en/merics-briefs/chip-controls',
            'https://www.eria.org/publications/supply-chain-resilience',
            'https://www.iseas.edu.sg/articles-commentaries/iseas-perspective/2026-73-economic-security/',
        ]
        bad=[
            'https://www.piie.com/experts/research-analysts/example',
            'https://www.piie.com/research/piie-charts',
            'https://www.bruegel.org/working-paper',
            'https://www.bruegel.org/people/example',
            'https://merics.org/en/analysis',
            'https://merics.org/en/industrial-policy-and-technology',
            'https://www.eria.org/publications/category/research-project-reports',
            'https://www.iseas.edu.sg/articles-commentaries/iseas-perspective/',
            'https://www.iseas.edu.sg/media/event-highlights/example/',
        ]
        for url in good:
            with self.subTest(url=url):
                self.assertTrue(source_discovery.looks_like_content_url(url))
                self.assertFalse(source_discovery.native_rejection_reason(url))
        for url in bad:
            with self.subTest(url=url):
                self.assertFalse(source_discovery.looks_like_content_url(url))
                self.assertTrue(source_discovery.native_rejection_reason(url))
        self.assertFalse(source_discovery.looks_like_content_url('https://bruegel.org.evil.example/working-paper/example'))

    def test_index_keeps_working_paper_but_not_staff_link(self):
        source=next(s for s in config.DISCOVERY_SOURCES if s['domain']=='bruegel.org')
        html='<main><a href="/working-paper/chip-access">Strategic access to chips</a><a href="/people/example">A researcher profile</a></main>'
        with patch.object(source_discovery,'fetch_text',return_value=(html,'ok')),patch.object(source_discovery.time,'sleep'):
            rows,_,_=source_discovery.scan_index_pages(source,'2026-10-14')
        self.assertEqual([r['url'] for r in rows],['https://www.bruegel.org/working-paper/chip-access'])

    def test_publisher_dates_ignore_related_article_dates(self):
        cases=[
            ('bruegel.org','<div class="c-single-header__meta-row"><dt>Publishing date</dt><dd>12 October 2026</dd></div>','2026-10-12'),
            ('iseas.edu.sg','<meta name="citation_publication_date" content="8 October 2026">','2026-10-08'),
            ('merics.org','<div class="layout__region--left"><h1>Economic security</h1><div class="field-name-field-date-published"><time datetime="2026-10-09T10:57:40Z">Oct 09, 2026</time></div></div>','2026-10-09'),
        ]
        for host,html,expected in cases:
            with self.subTest(host=host):
                soup=BeautifulSoup(html+'<aside><time datetime="2026-10-14">Related article</time></aside>','html.parser')
                date,detail,_=publication_dates.extract(soup,'https://'+host+'/example',content_extractor.parse_date)
                self.assertEqual(date.date().isoformat(),expected)
                self.assertTrue(detail.startswith(host.split('.')[0]))

    def test_conflicting_published_fields_fail_closed(self):
        html='<div class="layout__region--left"><h1>Economic security</h1><div class="field-name-field-date-published"><time datetime="2026-10-09"></time><time datetime="2026-10-10"></time></div></div>'
        date,_,_=publication_dates.extract(BeautifulSoup(html,'html.parser'),'https://merics.org/en/report/example',content_extractor.parse_date)
        self.assertIsNone(date)

if __name__=='__main__':unittest.main()
