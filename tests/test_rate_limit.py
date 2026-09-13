import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from llm import rate_limit as R
from llm.backend import BedrockBackend


class ConditionalError(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class TestGate(unittest.TestCase):
    def sdk(self, client):
        return patch.dict('sys.modules', {
            'boto3': types.SimpleNamespace(client=Mock(return_value=client)),
            'botocore.config': types.SimpleNamespace(Config=lambda **kw: kw),
        })

    def test_lambda_without_table_fails_closed(self):
        with patch.dict(os.environ, {'AWS_EXECUTION_ENV': 'AWS_Lambda_python3.12'}, clear=True):
            with self.assertRaises(R.RateLimitError):
                with R.acquire('us-east-1'): pass

    def test_lease_covers_call_and_release_sets_completion_cooldown(self):
        client = Mock()
        clock = [100.0]
        with self.sdk(client), patch.dict(os.environ, {'YOUTHSCOPE_RATE_TABLE': 'gate'}, clear=True), patch.object(R.time, 'time', side_effect=lambda: clock[0]):
            with R.acquire('us-east-1'):
                first = client.update_item.call_args.kwargs
                self.assertEqual(first['ExpressionAttributeValues'][':lease']['N'], '280000')
                self.assertEqual(client.update_item.call_count, 1)
                clock[0] = 105.0
            release = client.update_item.call_args.kwargs
            self.assertEqual(release['ExpressionAttributeValues'][':next']['N'], '106100')
            self.assertEqual(release['ExpressionAttributeValues'][':owner'], first['ExpressionAttributeValues'][':owner'])
            self.assertEqual(release['ConditionExpression'], '#owner = :owner')

    def test_model_failure_still_releases_with_cooldown(self):
        client = Mock()
        with self.sdk(client), patch.dict(os.environ, {'YOUTHSCOPE_RATE_TABLE': 'gate'}, clear=True):
            with self.assertRaises(ValueError):
                with R.acquire('us-east-1'): raise ValueError('model failed')
        self.assertEqual(client.update_item.call_count, 2)
        self.assertIn('REMOVE', client.update_item.call_args.kwargs['UpdateExpression'])

    def test_release_failure_surfaces_and_does_not_retry(self):
        client = Mock(); client.update_item.side_effect = [{}, OSError('network')]
        with self.sdk(client), patch.dict(os.environ, {'YOUTHSCOPE_RATE_TABLE': 'gate'}, clear=True):
            with self.assertRaises(R.RateLimitError):
                with R.acquire('us-east-1'): pass
        self.assertEqual(client.update_item.call_count, 2)

    def test_ddb_errors_fail_closed_without_retry(self):
        client = Mock(); client.update_item.side_effect = OSError('network')
        with self.sdk(client), patch.dict(os.environ, {'YOUTHSCOPE_RATE_TABLE': 'gate'}, clear=True):
            with self.assertRaises(R.RateLimitError):
                with R.acquire('us-east-1'): pass
        self.assertEqual(client.update_item.call_count, 1)

    def test_contention_has_bounded_wait(self):
        client = Mock(); client.update_item.side_effect = ConditionalError()
        clock = [100.0]
        with self.sdk(client), patch.dict(os.environ, {'YOUTHSCOPE_RATE_TABLE': 'gate'}, clear=True), \
                patch.object(R.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(R.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(R.time, 'sleep', side_effect=lambda n: clock.__setitem__(0, clock[0]+n)):
            with self.assertRaises(R.RateLimitError):
                with R.acquire('us-east-1'): pass
        self.assertLessEqual(clock[0], 120)

    def test_local_threads_are_serialized(self):
        starts = []
        with patch.dict(os.environ, {}, clear=True), patch.object(R, '_local_next', float('-inf')):
            def run():
                with R.acquire('us-east-1'):
                    starts.append(time.monotonic())
                    time.sleep(0.1)
            threads = [threading.Thread(target=run) for _ in range(3)]
            for t in threads: t.start()
            for t in threads: t.join()
        starts.sort()
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(b-a >= 1.19 for a,b in zip(starts, starts[1:])))

    def test_distributed_lease_prevents_delayed_response_start_bunching(self):
        client = Mock(); state = {}; lock = threading.Lock(); starts = []; finishes = []; errors = []
        def update(**kw):
            vals = kw['ExpressionAttributeValues']
            with lock:
                if ':lease' in vals:
                    now = int(vals[':now']['N'])
                    if state.get('lease', 0) >= now or state.get('next', 0) >= now:
                        raise ConditionalError()
                    state['lease'] = int(vals[':lease']['N'])
                    state['owner'] = vals[':owner']
                    slow = not starts
                else:
                    if state.get('owner') != vals[':owner']: raise ConditionalError()
                    state['next'] = int(vals[':next']['N'])
                    state.pop('lease', None); state.pop('owner', None)
                    return {}
            # Artificially slow successful acquisition response, after DB commit.
            if slow: time.sleep(0.5)
            return {}
        client.update_item.side_effect = update
        with self.sdk(client), patch.dict(os.environ, {'YOUTHSCOPE_RATE_TABLE': 'gate'}, clear=True):
            def run():
                try:
                    with R.acquire('us-east-1'):
                        starts.append(time.monotonic())
                        time.sleep(0.1)
                        finishes.append(time.monotonic())
                except Exception as exc: errors.append(type(exc).__name__)
            threads = [threading.Thread(target=run) for _ in range(2)]
            for t in threads: t.start()
            for t in threads: t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1]-finishes[0], 1.1)

    def test_converse_text_and_image_and_retry_configuration(self):
        client = Mock()
        client.converse.return_value = {'output': {'message': {'content': [{'text': 'hello'}, {'text': 'world'}]}}}
        with self.sdk(client), patch('llm.backend._load_dotenv'), patch.object(R, 'acquire') as gate:
            backend = BedrockBackend(model='test-model', region='us-west-2')
            import boto3
            with tempfile.TemporaryDirectory() as tmp:
                for ext, fmt in [('png','png'), ('jpg','jpeg'), ('jpeg','jpeg'), ('gif','gif'), ('webp','webp')]:
                    p=Path(tmp)/('img.'+ext);p.write_bytes(b'fixture')
                    self.assertEqual(backend.complete('question',p),'helloworld')
                    self.assertEqual(boto3.client.call_args.kwargs['config']['retries'], {'total_max_attempts': 1})
                    body=client.converse.call_args.kwargs
                    self.assertEqual(body['messages'][0]['content'],[{'image':{'format':fmt,'source':{'bytes':b'fixture'}}},{'text':'question'}])
            backend.complete('question', system='policy rules')
            self.assertEqual(client.converse.call_args.kwargs['system'], [{'text': 'policy rules'}])
            self.assertEqual(client.converse.call_args.kwargs['messages'][0]['content'], [{'text': 'question'}])
            gate.assert_called_with('us-west-2')
            gate.side_effect=R.RateLimitError('closed')
            before=client.converse.call_count
            with self.assertRaises(R.RateLimitError):backend.complete('question')
            self.assertEqual(client.converse.call_count,before)

    def test_ambiguous_timeout_retains_distributed_lease(self):
        client = Mock()
        with self.sdk(client), patch.dict(os.environ, {'YOUTHSCOPE_RATE_TABLE': 'gate'}, clear=True):
            with self.assertRaises(R.InferenceTimeout):
                with R.acquire('us-west-2'):
                    raise R.InferenceTimeout('lost response', may_be_running=True)
        self.assertEqual(client.update_item.call_count, 1)

    def test_expired_budget_never_acquires_gate(self):
        client = Mock()
        with self.sdk(client), R.request_budget(0):
            with self.assertRaises(R.InferenceTimeout):
                with R.acquire('us-west-2'): self.fail('must not start')
        client.update_item.assert_not_called()

    def test_multiple_calls_share_remaining_budget(self):
        client = Mock()
        client.converse.return_value = {'output': {'message': {'content': [{'text': 'ok'}]}}}
        clock = [100.0]
        with self.sdk(client), patch('llm.backend._load_dotenv'), patch.object(R, 'acquire'), \
                patch.object(R.time, 'monotonic', side_effect=lambda: clock[0]), R.request_budget(55):
            backend = BedrockBackend(model='test', region='us-west-2')
            import boto3
            backend.complete('first')
            self.assertEqual(boto3.client.call_args.kwargs['config']['read_timeout'], 48)
            clock[0] += 40
            backend.complete('second')
            self.assertEqual(boto3.client.call_args.kwargs['config']['read_timeout'], 10)
            clock[0] += 11
            with self.assertRaises(R.InferenceTimeout): backend.complete('third')
            self.assertEqual(client.converse.call_count, 2)
        self.assertEqual(R.remaining_budget(), 55)

    def test_socket_timeout_is_marked_ambiguous_and_not_retried(self):
        class ReadTimeoutError(Exception): pass
        client = Mock(); client.converse.side_effect = ReadTimeoutError()
        with self.sdk(client), patch('llm.backend._load_dotenv'), patch.object(R, 'acquire'):
            backend = BedrockBackend(model='test', region='us-west-2')
            with self.assertRaises(R.InferenceTimeout) as failure: backend.complete('question')
            self.assertTrue(failure.exception.inference_may_be_running)
            client.converse.assert_called_once()
            client.close.assert_called_once()


if __name__ == '__main__': unittest.main()
