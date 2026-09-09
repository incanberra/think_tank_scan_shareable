import unittest
from unittest.mock import patch, Mock
import requests
import ai_client, config
class ClientTests(unittest.TestCase):
    def test_timeout_retries_and_records_provider(self):
        run=Mock(model_requests=[])
        response=Mock(status_code=200);response.json.return_value={'id':'test','model':'test-model','provider':'test-provider','usage':{'total_tokens':10},'choices':[{'finish_reason':'stop','message':{'content':'{"analyses": []}'}}]}
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(ai_client.requests,'post',side_effect=[requests.Timeout('timed out'),response]) as call,patch.object(ai_client.time,'sleep'),patch('scan_runtime.current',return_value=run):
            self.assertEqual(ai_client.generate_json_with_retry('test-model',[]),{'analyses':[]})
            self.assertEqual(call.call_count,2);self.assertEqual(run.model_requests[-1]['provider'],'test-provider')
            self.assertTrue(call.call_args.kwargs['json']['provider']['require_parameters'])
    def test_auth_failure_is_not_retried(self):
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(ai_client.requests,'post',return_value=Mock(status_code=401)) as call:
            with self.assertRaisesRegex(RuntimeError,'401'):ai_client.generate_json_with_retry('model',[])
            self.assertEqual(call.call_count,1)
    def test_truncated_valid_json_is_not_accepted(self):
        response=Mock(status_code=200);response.json.return_value={'choices':[{'finish_reason':'length','message':{'content':'{"analyses": []}'}}]}
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(ai_client.requests,'post',return_value=response):
            with self.assertRaisesRegex(ValueError,'Incomplete'):ai_client.generate_json_with_retry('model',[],max_retries=1)
if __name__=='__main__':unittest.main()
