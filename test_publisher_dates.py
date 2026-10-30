import unittest
from bs4 import BeautifulSoup
from content_extractor import parse_date
import publication_dates as dates
class PublisherDates(unittest.TestCase):
 def test_carnegie_current_page(self):
  html='<script id="page-analytics-data">var p={"publication_date":"2026-10-21"};</script><time>October 30, 2026</time>'
  d,_,_=dates.extract(BeautifulSoup(html,'html.parser'),'https://carnegieendowment.org/events/test',parse_date)
  self.assertEqual(d.date().isoformat(),'2026-10-21')
 def test_no_unscoped_carnegie_date(self):
  html='<script>{"publication_date":"2026-10-21"}</script>'
  self.assertIsNone(dates.extract(BeautifulSoup(html,'html.parser'),'https://carnegieendowment.org/test',parse_date)[0])
 def test_hudson_hero_not_related(self):
  html='<div class="hero__content"><h1>Title</h1><div class="hero__content-top"><div class="field-date"><time datetime="2026-10-21">Oct 21, 2026</time></div></div></div><time>Oct 30, 2026</time>'
  soup=BeautifulSoup(html,'html.parser')
  self.assertEqual(dates.extract(soup,'https://www.hudson.org/foreign-policy/test',parse_date)[0].day,21)
  self.assertIsNone(dates.extract(soup,'https://www.hudson.org/events/test',parse_date)[0])
if __name__=='__main__':unittest.main()
