import base64
import json
import unittest
from unittest.mock import patch
from deploy.runtime import handler


class CloudRuntimeTests(unittest.TestCase):
    def event(self, payload, method='POST', path='/api'):
        return {'requestContext': {'http': {'method': method}}, 'rawPath': path, 'body': json.dumps(payload)}

    @patch('deploy.runtime._ai')
    def test_pipeline_cannot_run(self, ai):
        self.assertEqual(handler(self.event({'action': 'pipeline'}))['statusCode'], 400)
        ai.assert_not_called()

    @patch('deploy.runtime._ai', return_value={'ok': True, 'text': '測試回答'})
    def test_known_action_uses_same_contract(self, ai):
        body = {'action': 'advise', 'question': '新北的青年趨勢？', 'region': '新北市'}
        response = handler(self.event(body))
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(json.loads(response['body'])['text'], '測試回答')
        ai.assert_called_once_with('advise', body)

    @patch('deploy.runtime._ai')
    def test_invalid_body_and_fields_do_not_invoke(self, ai):
        for body in [[], {'action': []}, {'action': 'ask', 'question': 12},
                     {'action': 'scan', 'suffix': '../../file', 'image_base64': 'YWJj'}]:
            self.assertEqual(handler(self.event(body))['statusCode'], 400)
        ai.assert_not_called()

    @patch('deploy.runtime._ai')
    def test_health_does_not_invoke(self, ai):
        self.assertEqual(handler(self.event({}, method='GET'))['statusCode'], 200)
        ai.assert_not_called()

    @patch('deploy.runtime._ai', return_value={'ok': True})
    def test_base64_function_url_event(self, ai):
        event = self.event({'action': 'ask', 'question': '人口'})
        event['body'] = base64.b64encode(event['body'].encode()).decode()
        event['isBase64Encoded'] = True
        self.assertEqual(handler(event)['statusCode'], 200)

    @patch('deploy.runtime._ai', side_effect=RuntimeError('do not expose internal details'))
    def test_error_is_sanitized(self, ai):
        result = handler(self.event({'action': 'advise', 'question': '問題'}))
        self.assertEqual(result['statusCode'], 503)
        self.assertNotIn('do not expose', result['body'])

if __name__ == '__main__':
    unittest.main()
