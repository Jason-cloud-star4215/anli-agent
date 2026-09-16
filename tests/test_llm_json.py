"""大模型返回的 JSON 处理：截断要重试，失败要计数（不能静默退回规则版）。"""

import unittest

from anli.llm import LLMClient, LLMError, extract_json


class StubClient(LLMClient):
    """不发网络请求的测试桩：按脚本返回文本。"""

    def __init__(self, replies):
        super().__init__(api_key="test-key")
        self.replies = list(replies)
        self.prompts = []

    def chat(self, system, user, temperature=0.2, max_tokens=1500):  # noqa: D102
        self.prompts.append(user)
        self.calls += 1
        return self.replies.pop(0) if self.replies else ""


class ExtractJsonTest(unittest.TestCase):
    def test_code_fence_and_extra_text(self):
        payload = extract_json('说明文字\n```json\n{"summary": "ok", "bullets": ["a"]}\n```')
        self.assertEqual(payload["summary"], "ok")

    def test_truncated_json_raises_llm_error(self):
        with self.assertRaises(LLMError):
            extract_json('{"summary": "x", "bullets": ["a"')


class ChatJsonTest(unittest.TestCase):
    def test_retries_after_broken_json(self):
        client = StubClient(['{"summary": "被截断', '{"summary": "完整", "bullets": ["a"]}'])
        payload = client.chat_json("系统", "用户")
        self.assertEqual(payload["summary"], "完整")
        self.assertEqual(len(client.prompts), 2)
        self.assertEqual(client.failures, 0)

    def test_counts_failure_when_all_attempts_broken(self):
        client = StubClient(['{"a": ', '还是坏的'])
        with self.assertRaises(LLMError):
            client.chat_json("系统", "用户")
        self.assertEqual(client.failures, 1)

    def test_unavailable_client_raises(self):
        client = LLMClient(api_key="")
        self.assertFalse(client.available)
        with self.assertRaises(LLMError):
            client.chat("系统", "用户")


if __name__ == "__main__":
    unittest.main()
