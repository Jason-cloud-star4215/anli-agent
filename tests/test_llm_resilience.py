"""大模型韧性测试：带刹车重试 + 熔断 + 失败留痕。

全部用假 urlopen 模拟，不打真实 API：
- 429/5xx/连接类失败自动重试一次；401/402 等配置性问题不重试；
- 超时类失败用缩水超时（30s）重试；
- 同一任务连续 2 次最终失败即熔断，后续调用秒退、不再发起请求。
"""

import io
import json
import socket
import unittest
import urllib.error
from unittest import mock

from anli.llm import LLMClient, LLMError


class FakeResponse:
    def __init__(self, content: str):
        self._data = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError(
        "https://fake", code, "err", headers, io.BytesIO(b'{"error":"x"}')
    )


def make_client() -> LLMClient:
    client = LLMClient(base_url="https://fake", api_key="sk-test", model="test")
    client.retry_backoff = 0  # 测试里不真的等
    return client


class RetryTest(unittest.TestCase):
    def test_retry_recovers_from_429(self):
        calls = {"n": 0, "timeouts": []}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            calls["timeouts"].append(timeout)
            if calls["n"] == 1:
                raise http_error(429, retry_after="0")
            return FakeResponse("恢复成功")

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            text = client.chat("s", "u")
        self.assertEqual(text, "恢复成功")
        self.assertEqual(calls["n"], 2, "429 应自动重试一次")
        self.assertEqual(client.retries, 1)
        self.assertEqual(client.consecutive_failures, 0, "成功后连续失败计数应清零")
        self.assertTrue(client.available)
        self.assertEqual(client.failures, 0)

    def test_no_retry_on_401(self):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            raise http_error(401)

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(LLMError):
                client.chat("s", "u")
        self.assertEqual(calls["n"], 1, "401 是配置性问题，不应重试")
        self.assertEqual(client.retries, 0)
        self.assertIn("401", client.last_failure)

    def test_timeout_retry_uses_reduced_timeout(self):
        calls = {"n": 0, "timeouts": []}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            calls["timeouts"].append(timeout)
            if calls["n"] == 1:
                raise socket.timeout("timed out")
            return FakeResponse("超时后恢复")

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            text = client.chat("s", "u")
        self.assertEqual(text, "超时后恢复")
        self.assertEqual(calls["timeouts"], [90, 30], "重试应使用缩水超时（30s）")


class BreakerTest(unittest.TestCase):
    def test_breaker_opens_and_short_circuits(self):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            raise socket.timeout("timed out")

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(LLMError):
                client.chat("s", "u")  # 2 次尝试，1 次最终失败
            with self.assertRaises(LLMError):
                client.chat("s", "u")  # 再 2 次尝试，第 2 次最终失败 → 熔断
            attempts_before = calls["n"]
            self.assertFalse(client.available, "熔断后 available 应为 False")
            with self.assertRaises(LLMError):
                client.chat("s", "u")  # 秒退，不再发起请求
        self.assertEqual(calls["n"], attempts_before, "熔断后不得再发起 HTTP 请求")
        self.assertTrue(client.circuit_open)

    def test_success_resets_breaker_state(self):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise socket.timeout("timed out")
            return FakeResponse("ok")

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            client.chat("s", "u")
        self.assertEqual(client.consecutive_failures, 0)
        self.assertFalse(client.circuit_open)


class FailureReasonTest(unittest.TestCase):
    def test_json_failure_is_recorded(self):
        def fake_urlopen(request, timeout=None):
            return FakeResponse("这不是 JSON")

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(LLMError):
                client.chat_json("s", "u")
        self.assertEqual(client.failures, 1)
        self.assertTrue(client.last_failure.startswith("JSON 解析失败"))

    def test_timeout_reason_is_human_readable(self):
        def fake_urlopen(request, timeout=None):
            raise socket.timeout("timed out")

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(LLMError):
                client.chat("s", "u")
        self.assertEqual(client.last_failure, "请求超时")


def response_with_usage(content: str, prompt: int, completion: int):
    """带 usage 的假响应：用于核对逐次调用的 token 留痕。"""

    body = json.dumps(
        {
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }
    ).encode("utf-8")

    class _Response:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Response()


class CallLogTest(unittest.TestCase):
    """逐次调用留痕：环节名、第几次尝试、耗时与 token 都要如实记录。

    回归点：chat() 内部原本用同名变量跑 HTTP 重试循环，把 attempt 参数覆盖成 0/1，
    导致「JSON 校验失败后的重试」在调用明细里永远显示成「第 1 次」。
    """

    def test_records_purpose_attempt_and_usage(self):
        client = make_client()
        with mock.patch(
            "anli.llm.urllib.request.urlopen",
            return_value=response_with_usage("ok", 100, 20),
        ):
            client.chat("s", "u", purpose="质证复核", attempt=1)
            client.chat("s", "u", purpose="质证复核", attempt=2)
        self.assertEqual([item["index"] for item in client.call_log], [1, 2])
        self.assertEqual([item["purpose"] for item in client.call_log], ["质证复核", "质证复核"])
        self.assertEqual([item["attempt"] for item in client.call_log], [1, 2], "第几次尝试必须如实记录")
        self.assertEqual([item["prompt_tokens"] for item in client.call_log], [100, 100])
        self.assertEqual([item["completion_tokens"] for item in client.call_log], [20, 20])
        self.assertEqual(client.usage["prompt_tokens"], 200, "逐次 token 之和应等于累计用量")

    def test_http_retry_keeps_json_attempt_number(self):
        """HTTP 层重试（429）成功后，记录的第几次尝试仍应是 JSON 层传入的值。"""

        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http_error(429, retry_after="0")
            return FakeResponse("ok")

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            client.chat("s", "u", purpose="检索词规划", attempt=2)
        self.assertEqual(client.call_log[0]["attempt"], 2)

    def test_json_retry_records_two_attempts(self):
        replies = ["这不是 JSON", '{"ok": 1}']

        def fake_urlopen(request, timeout=None):
            return FakeResponse(replies.pop(0))

        client = make_client()
        with mock.patch("anli.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            payload = client.chat_json("s", "u", purpose="质证复核")
        self.assertEqual(payload, {"ok": 1})
        self.assertEqual(
            [item["attempt"] for item in client.call_log],
            [1, 2],
            "JSON 解析失败重试后，明细里应出现第 1、2 两次尝试",
        )


if __name__ == "__main__":
    unittest.main()
