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
    def test_reasoning_is_opt_in_and_audited(self):
        response=Mock(status_code=200)
        response.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':'{"analyses": []}'}}]}
        for effort in ('', 'low'):
            run=Mock(model_requests=[])
            with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(config,'REVIEW_REASONING_EFFORT',effort),patch.object(ai_client.requests,'post',return_value=response) as call,patch('scan_runtime.current',return_value=run):
                ai_client.generate_json_with_retry('model',[])
            expected={'effort':'low'} if effort else None
            self.assertEqual(call.call_args.kwargs['json'].get('reasoning'),expected)
            self.assertEqual(run.model_requests[0]['reasoning'],expected)
    def test_provider_preference_is_opt_in_and_preserves_parameter_checks(self):
        response=Mock(status_code=200)
        response.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':'{"topics": []}'}}]}
        for options in (None, {'order':['together','siliconflow'],'ignore':['wafer'],'require_parameters':False}):
            run=Mock(model_requests=[])
            with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(ai_client.requests,'post',return_value=response) as call,patch('scan_runtime.current',return_value=run):
                ai_client.generate_json_with_retry('model',[],provider_options=options)
            provider=call.call_args.kwargs['json']['provider']
            self.assertTrue(provider['require_parameters'])
            self.assertEqual(provider.get('ignore'),['wafer'] if options else None)
            self.assertEqual(run.model_requests[0]['provider_preferences'],provider)
    def test_response_schema_is_opt_in(self):
        response=Mock(status_code=200)
        response.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':'{"topics": {}}'}}]}
        schema={'type':'object','properties':{'topics':{'type':'object'}},'required':['topics']}
        with patch.object(config,'OPENROUTER_API_KEY','test'),patch.object(ai_client.requests,'post',return_value=response) as call:
            ai_client.generate_json_with_retry('model',[],response_schema=schema)
            self.assertEqual(call.call_args.kwargs['json']['response_format']['json_schema']['schema'],schema)
            self.assertTrue(call.call_args.kwargs['json']['response_format']['json_schema']['strict'])
if __name__=='__main__':unittest.main()
