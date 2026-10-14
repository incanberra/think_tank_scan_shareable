import copy,tempfile,unittest
from pathlib import Path
from bs4 import BeautifulSoup
import editorial_report

def article(title,score,tier='main'):
    return dict(title=title,institution='Center for Strategic and International Studies (CSIS)',
                url='https://example.org/'+title,date='14 October 2026',importance_score=score,
                editorial_tier=tier,tags=['Critical Minerals','Economic Coercion','Emerging Technologies'],
                summary='Why it matters:\nSupplier concentration creates strategic dependence. A second implication.\n\nSummary:\nThe U.S. faces concentrated supply. Allied investments diversify suppliers. A third sentence remains in the audit.')

class DailyReadability(unittest.TestCase):
    def test_priority_digest_preserves_input_and_supporting_tier(self):
        data={'reports':[article('support',5,'further_reading'),article('low',1),article('highest',5),article('medium',3),article('second',4)]}
        original=copy.deepcopy(data)
        c=editorial_report.context(data,'2026-10-14')
        self.assertEqual([i['title'] for i in c['takeaways']],['highest','second','medium'])
        self.assertEqual([i['section_heading'] for i in c['items']],['Priority reading','','','More analysis','Further reading'])
        self.assertEqual(c['items'][-1]['editorial_tier'],'further_reading')
        self.assertEqual(data,original)
        self.assertEqual(c['items'][0]['summary'],'The U.S. faces concentrated supply. Allied investments diversify suppliers.')
        self.assertEqual(c['items'][0]['why_it_matters'],'Supplier concentration creates strategic dependence.')
        self.assertEqual(len(c['items'][0]['topic_pills']),2)
        self.assertEqual(c['items'][0]['hidden_topic_count'],1)

    def test_empty_edition_and_actual_source_health(self):
        audit={'source_health':{'sources':[{'source':'Chatham House','availability':'unavailable'}, {'source':'NBER','availability':'empty'}]}}
        c=editorial_report.context({},'2026-10-14',audit)
        self.assertEqual(c['takeaways'],[])
        self.assertEqual(c['source_count'],2)
        self.assertTrue(c['coverage_limited'])
        self.assertEqual(c['unavailable'],['Chatham House'])
        html=editorial_report.generate_html({},'2026-10-14',{},recall_audit=audit)
        self.assertIn('No verified items',html)
        self.assertNotIn('At a glance',html)

    def test_untrusted_markup_and_urls_never_executable(self):
        row=article('<script>alert(1)</script>',4)
        row['url']='javascript:alert(1)'
        html=editorial_report.generate_html({'reports':[row]},'2026-10-14',{})
        soup=BeautifulSoup(html,'html.parser')
        self.assertEqual(soup.find_all('script'),[])
        self.assertFalse(any(a.get('href','').startswith('javascript:') for a in soup.find_all('a')))

    def test_event_time_uses_local_label_and_date_precision(self):
        row=article('event',3)
        row.update(event_start_at='2026-10-20T13:30:00+11:00')
        c=editorial_report.context({'events':[row]},'2026-10-14')
        self.assertEqual(c['items'][0]['date_label'],'Starts 20 Oct 2026, 13:30 AEDT')
        row['event_start_precision']='date'
        c=editorial_report.context({'events':[row]},'2026-10-14')
        self.assertEqual(c['items'][0]['date_label'],'Starts 20 Oct 2026')

    def test_pdf_includes_every_source_link_and_reading_section(self):
        import fitz
        rows=[article('item'+str(i),i) for i in range(1,5)]+[article('support',5,'further_reading')]
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'report.pdf'
            editorial_report.generate_pdf({'reports':rows},'2026-10-14',{},str(file))
            doc=fitz.open(file)
            urls={a['uri'] for page in doc for a in page.get_links() if 'uri' in a}
            self.assertEqual(urls,{r['url'] for r in rows})
            text='\n'.join(p.get_text() for p in doc)
            for name in ('At a glance','Priority reading','More analysis','Further reading','Coverage and method'):
                self.assertIn(name,text)
            self.assertNotIn('Priority 5 / 5',text)
            doc.close()
