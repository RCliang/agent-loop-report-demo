"""验证最小原生工具循环；网络边界使用已知响应，不请求在线 API。"""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from deepseek_model import DeepSeekModel
from minimal_loop import minimal_loop, run_minimal
from report_service import ReportService


def response(calls=(), text=None):
    message = {'role': 'assistant', 'content': text}
    if calls:
        message['tool_calls'] = [{'id': cid, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(args)}} for cid, name, args in calls]
    return {'id': 'response-test', 'model': 'deepseek-flash', 'choices': [
        {'message': message, 'finish_reason': 'tool_calls' if calls else 'stop'}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}


class MinimalLoopTests(unittest.TestCase):
    def model(self, replies):
        self.assertTrue(callable(getattr(DeepSeekModel, 'chat', None)), '缺少原生 chat API 接口')
        pending = iter(replies)
        self.payloads = []
        def transport(payload):
            self.payloads.append(deepcopy(payload))
            return next(pending)
        return DeepSeekModel(api_key='unit-test-credential', transport=transport)

    def test_actual_tool_result_enters_next_native_request(self):
        model = self.model([response([('policy-id', 'read_policy', {})]), response(text='已读取')])
        service = ReportService(Path(__file__).parent/'fixtures')
        answer = minimal_loop('读取统计口径', model, service.dispatch)
        self.assertEqual(answer, '已读取')
        result = self.payloads[-1]['messages'][-1]
        self.assertEqual(result['role'], 'tool')
        self.assertEqual(result['tool_call_id'], 'policy-id')
        self.assertEqual(json.loads(result['content'])['period'], ['2026-10-19', '2026-10-25'])

    def test_all_calls_in_one_response_are_returned_before_next_request(self):
        model = self.model([response([('one', 'list_files', {}), ('two', 'read_policy', {})]), response(text='已读取')])
        service = ReportService(Path(__file__).parent/'fixtures')
        answer = minimal_loop('读取文件清单和口径', model, service.dispatch)
        self.assertEqual(answer, '已读取')
        returned = [m for m in self.payloads[-1]['messages'] if m['role']=='tool']
        self.assertEqual([m['tool_call_id'] for m in returned], ['one', 'two'])
        self.assertEqual(model.metadata()['model_calls'], 2)

    def test_final_text_returns_without_business_completion_gate(self):
        model = self.model([response(text='当前信息不足，请补充表格')])
        executed = []
        answer = minimal_loop('处理表格', model, lambda name, args: executed.append(name))
        self.assertEqual(answer, '当前信息不足，请补充表格')
        self.assertEqual(executed, [])
        self.assertEqual(len(self.payloads), 1)

    def test_default_run_uses_real_adapter_instead_of_fixed_action_route(self):
        model = self.model([response([('policy-id', 'read_policy', {})]), response(text='已读取口径')])
        with patch('minimal_loop.DeepSeekModel', return_value=model):
            result = run_minimal(Path(__file__).parent/'fixtures')
        self.assertEqual(result['engine'], 'deepseek')
        self.assertEqual(result['model_calls'], 2)
        self.assertEqual(result['tool_calls'], 1)
        self.assertEqual(result['answer'], '已读取口径')
        self.assertFalse(result['business_verified'])
        self.assertTrue(result['source_unchanged'])


if __name__=='__main__':
    unittest.main()
