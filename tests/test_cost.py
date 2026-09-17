"""成本估算测试：有单价才给金额，没单价不许猜。"""

import unittest
from pathlib import Path
from unittest import mock

from anli.cost import DEFAULT_CURRENCY, load_pricing, render_line, render_note, summarize
from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"

PRICING = {"input_per_million": 2.0, "output_per_million": 8.0, "currency": "CNY"}


class _FakeLLM:
    def __init__(self, calls=0, failures=0, attempts=None, seconds=0.0, usage=None):
        self.calls = calls
        self.failures = failures
        self.attempts = attempts if attempts is not None else calls
        self.seconds = seconds
        self.usage = usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class CostSummaryTest(unittest.TestCase):
    def test_rules_only_run_reports_zero(self):
        summary = summarize(None, pricing=PRICING, pricing_source="测试")
        self.assertEqual(summary.calls, 0)
        self.assertEqual(summary.cost, 0.0)
        self.assertIn("未调用大模型", summary.text)

    def test_missing_pricing_does_not_guess(self):
        llm = _FakeLLM(calls=8, seconds=42.3)
        summary = summarize(llm, pricing=None, pricing_source="未配置")
        self.assertEqual(summary.calls, 8)
        self.assertIsNone(summary.cost, "没有单价就不该给出金额")
        self.assertIn("未配置单价", summary.text)
        self.assertIn("42.3", summary.text)

    def test_cost_is_computed_from_pricing_and_usage(self):
        llm = _FakeLLM(
            calls=8,
            seconds=42.3,
            usage={"prompt_tokens": 10000, "completion_tokens": 2000, "total_tokens": 12000},
        )
        summary = summarize(llm, pricing=PRICING, pricing_source="pricing.json")
        # 10000/1e6*2 + 2000/1e6*8 = 0.02 + 0.016
        self.assertAlmostEqual(summary.cost, 0.036, places=4)
        self.assertIn("估算成本", summary.text)
        self.assertIn("CNY", summary.text)

    def test_pricing_without_usage_says_so(self):
        llm = _FakeLLM(calls=5, seconds=10.0)
        summary = summarize(llm, pricing=PRICING, pricing_source="pricing.json")
        self.assertIsNone(summary.cost)
        self.assertIn("token 用量", summary.text)

    def test_failures_and_attempts_are_counted(self):
        llm = _FakeLLM(calls=3, failures=2, attempts=5, seconds=12.5)
        summary = summarize(llm, pricing=PRICING, pricing_source="测试")
        self.assertEqual(summary.attempts, 5)
        self.assertEqual(summary.failures, 2)
        self.assertIn("失败 2 次", summary.text)

    def test_attempts_defaults_to_calls(self):
        """老客户端没有 attempts 字段时不能报 0 次请求。"""

        llm = _FakeLLM(calls=4)
        del llm.attempts
        self.assertEqual(summarize(llm, pricing=PRICING, pricing_source="测试").attempts, 4)

    def test_note_is_a_reference_not_a_bill(self):
        summary = summarize(_FakeLLM(calls=1), pricing=PRICING, pricing_source="测试")
        self.assertIn("参考值", render_note(summary))
        self.assertIn("账单", render_note(summary))

    def test_render_line_is_plain_sentence(self):
        line = render_line(summarize(None, pricing=PRICING, pricing_source="测试"))
        self.assertTrue(line.startswith("本次分析："))
        self.assertTrue(line.endswith("。"))


class PricingLoadingTest(unittest.TestCase):
    def test_environment_variables_win(self):
        env = {
            "ANLI_PRICE_INPUT_PER_MILLION": "1.5",
            "ANLI_PRICE_OUTPUT_PER_MILLION": "6",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            pricing, source = load_pricing()
        self.assertEqual(source, "环境变量")
        self.assertEqual(pricing["input_per_million"], 1.5)
        self.assertEqual(pricing["currency"], DEFAULT_CURRENCY)

    def test_missing_pricing_reports_unconfigured(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            pricing, source = load_pricing()
        # 仓库里不放 pricing.json，所以正常就是「未配置」
        self.assertIsNone(pricing)
        self.assertEqual(source, "未配置")


class CostInTaskTest(unittest.TestCase):
    """成本行要出现在交付物里，不是只在日志里。"""

    def test_summary_artifact_carries_cost_line(self):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="cost-1",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        summary = next(a for a in record.artifacts if a.kind == "summary")
        self.assertIn("本次分析：", summary.body)
        # 规则版没有调用模型，成本行要如实写成「未调用大模型」，而不是报 0 次调用
        self.assertIn("未调用大模型", summary.body)
        self.assertIn("模型成本 0 元", summary.body)
        self.assertIn("cost", record.extra)
        self.assertEqual(record.extra["cost"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
