import copy,unittest
from unittest.mock import Mock,patch
import ai_client,config,main,analyzer
from test_review_improvements import positive_decision
from test_scanner_reliability import publication

class HaikuRecovery(unittest.TestCase):
 def test_temperature_omitted_only_for_haiku(self):
  sent=[]
  response=Mock(status_code=200)
  response.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
  def post(*args,**kwargs):sent.append(copy.deepcopy(kwargs['json']));return response
  with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(ai_client.requests,'post',side_effect=post),patch('scan_runtime.current',return_value=None):
   ai_client.generate_json_with_retry('anthropic/claude-haiku-5.5',[])
   ai_client.generate_json_with_retry('z-ai/glm-5.3-flash',[])
  self.assertNotIn('temperature',sent[0]);self.assertEqual(sent[1]['temperature'],.1)
  self.assertTrue(sent[0]['provider']['require_parameters'])

 def test_failed_reviews_cannot_be_sent_as_clean_empty_edition(self):
  results={'haiku':{'reports':[],'excluded':[],'needs_review':[{'exclusion_reason':'model_review_failed: OpenRouter HTTP 404'}]}}
  with self.assertRaisesRegex(RuntimeError,'email blocked'):main.require_completed_review(results)
  # Real negative relevance decisions are a legitimate empty edition.
  main.require_completed_review({'glm':{'excluded':[{'exclusion_reason':'Outside scope'}]}})
  main.require_completed_review({'glm':{'reports':[{'title':'A verified item'}],'needs_review':results['haiku']['needs_review']}})

 def test_permanent_routing_error_not_split_into_repeated_calls(self):
  items=[publication(url=f'https://example.org/research/{n}',title=f'Distinct supply chain research number {n}',extracted_text='A substantive strategic supply chain analysis. '*30,evidence_quality='sufficient') for n in range(3)]
  with patch.object(config,'OPENROUTER_API_KEY','test'),patch('scan_runtime.current',return_value=None),patch.object(ai_client,'generate_json_with_retry',side_effect=RuntimeError('OpenRouter HTTP 404')) as request:
   data=analyzer.analyze_items(items,'anthropic/claude-haiku-5.5')
  self.assertEqual(request.call_count,1);self.assertEqual(len(data['needs_review']),3)
