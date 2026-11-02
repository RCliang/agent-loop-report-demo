"""用已知网络响应检查真实模型适配器，不消耗在线 API。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest
import urllib.error

from loop import run_report
from minimal_loop import minimal_loop
from report_service import ReportService


KEY = 'unit-test-credential'


def reply(name=None, args=None, content=None, call_id='call_1'):
    message = {'role': 'assistant', 'content': content}
    if name:
        message['tool_calls'] = [{'id': call_id, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(args or {})}}]
    return {'id': 'test-response', 'model': 'deepseek-flash', 'created': 1,
            'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if name else 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}


class DeepSeekAdapterTests(unittest.TestCase):
    def model(self, responses, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('deepseek_model'), '缺少真实模型适配器')
        from deepseek_model import DeepSeekModel
        pending = iter(responses)
        self.payloads = []
        def transport(payload):
            self.payloads.append(deepcopy(payload))
            result = next(pending)
            if isinstance(result, Exception):
                raise result
            return result
        return DeepSeekModel(api_key=KEY, transport=transport, **kwargs)

    def test_tool_result_uses_the_actual_call_id_on_next_request(self):
        m = self.model([reply('list_files', call_id='real-id'), reply(content='结束')])
        self.assertEqual(m.decide([]), ('list_files', {}))
        self.assertEqual(m.decide([{'name': 'list_files', 'args': {}, 'result': {'ok': True, 'files': []}}]), ('final', {'text': '结束'}))
        messages = self.payloads[-1]['messages']
        self.assertEqual(messages[-1], {'role': 'tool', 'tool_call_id': 'real-id', 'content': '{"ok": true, "files": []}'})
        self.assertEqual(messages[-2]['tool_calls'][0]['id'], 'real-id')
        self.assertEqual(m.metadata()['model_calls'], 2)
        self.assertEqual(m.metadata()['usage']['total_tokens'], 30)

    def test_completion_rejection_is_sent_back_to_the_real_adapter(self):
        m = self.model([reply(content='完成'), reply('ask_user', {'text': '请补资料'})])
        r = run_report(Path(__file__).parent/'fixtures', model=m)
        self.assertEqual(r['status'], 'needs_input')
        self.assertEqual(r['model_calls'], 2)
        self.assertIn('缺少当前版本的验证记录', self.payloads[-1]['messages'][-1]['content'])
        self.assertEqual(self.payloads[-1]['messages'][-2]['content'], '完成')

    def test_multiple_calls_are_all_executed_before_next_network_request(self):
        batch = reply('list_files', call_id='one')
        batch['choices'][0]['message']['tool_calls'] += reply('read_policy', call_id='two')['choices'][0]['message']['tool_calls']
        m = self.model([batch, reply(content='已读取')])
        service = ReportService(Path(__file__).parent/'fixtures')
        answer = minimal_loop('读取目录和口径', m, service.dispatch)
        self.assertEqual(answer, '已读取')
        self.assertEqual(service.seen, {'basis.json'})
        self.assertEqual(m.metadata()['model_calls'], 2)
        results = [x for x in self.payloads[-1]['messages'] if x['role']=='tool']
        self.assertEqual([x['tool_call_id'] for x in results], ['one', 'two'])

    def test_invalid_arguments_stop_without_executing_a_tool(self):
        bad = reply('read_table')
        bad['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = '[]'
        m = self.model([bad])
        r = run_report(Path(__file__).parent/'fixtures', model=m)
        self.assertEqual(r['status'], 'model_error')
        self.assertEqual(r['tool_calls'], 0)
        self.assertIsNone(r['report'])
        self.assertEqual(r['model_calls'], 1)

    def test_http_error_is_recorded_without_credentials_or_remote_error_body(self):
        failure = urllib.error.HTTPError('https://api.deepseek.com/chat/completions', 401, KEY, {}, None)
        m = self.model([failure])
        r = run_report(Path(__file__).parent/'fixtures', model=m)
        self.assertEqual(r['status'], 'model_error')
        self.assertEqual(r['real_model_runs'], 0)
        self.assertEqual(r['model_calls'], 1)
        self.assertNotIn(KEY, json.dumps({'result': r, 'requests': m.requests}))

    def test_native_response_and_usage_are_retained_but_key_is_absent(self):
        m = self.model([reply(content='已完成')])
        m.decide([])
        record = m.requests[0]
        self.assertEqual(record['response']['id'], 'test-response')
        self.assertEqual(record['request']['model'], 'deepseek-flash')
        self.assertEqual(record['request']['thinking'], {'type': 'disabled'})
        self.assertNotIn(KEY, json.dumps({'records': m.requests, 'metadata': m.metadata()}))

    def test_unknown_tool_is_rejected_at_network_boundary(self):
        m = self.model([reply('delete_everything')])
        r = run_report(Path(__file__).parent/'fixtures', model=m)
        self.assertEqual(r['status'], 'model_error')
        self.assertEqual(r['tool_calls'], 0)

    def test_credentials_cannot_be_sent_to_another_host(self):
        self.assertIsNotNone(importlib.util.find_spec('deepseek_model'), '缺少真实模型适配器')
        from deepseek_model import DeepSeekModel
        with self.assertRaises(ValueError):
            DeepSeekModel(api_key=KEY, base_url='https://example.com')


if __name__ == '__main__':
    unittest.main()
