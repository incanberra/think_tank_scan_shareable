import unittest,json
from iiss_public import render_public_components
from bs4 import BeautifulSoup
from content_extractor import extract_text_from_html
class AccessTests(unittest.TestCase):
 def test_body_and_date(self):
  data=[('ArticleNav',{'Current':{'Date':'19th October 2026'}}),('Reading',{'Html':'<div>'+('Public economic evidence sentence. '*30)+'</div>'}),('FeatureListCarousel',{'Html':'RELATED MUST NOT APPEAR'})]
  html='<html><body>'+''.join('<script>React.createElement(Components.'+k+', '+json.dumps(v)+')</script>' for k,v in data)+'</body></html>'
  out=render_public_components(html);text=extract_text_from_html(out)
  self.assertGreater(len(text),500);self.assertNotIn('RELATED',text);self.assertIn('datetime="2026-10-19"',out)
 def test_listing(self):
  html='<body><script>React.createElement(Components.Filter, '+json.dumps({'Items':[{'Heading':'Economic security','Link':'/online-analysis/2026/10/test/','Date':'19th October 2026'}]})+')</script></body>'
  soup=BeautifulSoup(render_public_components(html),'html.parser');self.assertEqual(soup.a['href'],'/online-analysis/2026/10/test/')
if __name__ == '__main__':
 unittest.main()
