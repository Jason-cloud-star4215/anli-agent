"""量级闸门测试：样本判赔量级与本案不匹配时，拒绝区间外推（诚实降级）。

回归背景：8 万医疗费的重伤案曾匹配到 5000 元级演示样本，
把「最可能落点」拉到 5,125–5,675 元，与同屏「可支持 56,000 元」自相矛盾。
"""

import unittest
from pathlib import Path

from anli.feasibility import build_assessment
from anli.intake import build_profile
from anli.models import CaseRecord, Role
from anli.orchestrator import Orchestrator
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def make_case(index: int, amount: float) -> CaseRecord:
    return CaseRecord(
        case_id=f"demo-{index}",
        court="演示法院",
        year=2025,
        region="浙江省",
        case_type="交通事故人身损害赔偿",
        dispute="赔偿金额",
        holding="法院按责任比例支持部分赔偿请求。",
        amounts={"合计": amount},
        synthetic=True,
    )


class MagnitudeGateUnitTest(unittest.TestCase):
    def test_big_case_rejects_small_samples(self):
        """8 万医疗费（可支持 56,000）遇到 5000 元级样本：拒绝外推。"""
        cases = [make_case(i, amount) for i, amount in enumerate([5000, 5400, 5600, 5450])]
        result = build_assessment(
            expectation=900000,
            legal_cap=80000,
            cases=cases,
            responsibility_ratio=0.7,
            claimed_amount=80000,
        )
        self.assertTrue(result.magnitude_mismatch)
        self.assertIsNone(result.case_median)
        self.assertIsNone(result.likely_low)
        self.assertIsNone(result.likely_high)
        self.assertEqual(result.legal_cap, 56000)
        self.assertTrue(any("量级" in reason for reason in result.gap_reasons))

    def test_small_case_keeps_interval(self):
        """小额案情与小额样本同档：区间照常输出（演示流程不受影响）。"""
        cases = [make_case(i, amount) for i, amount in enumerate([5000, 5400, 5600, 5450])]
        result = build_assessment(
            expectation=12000,
            legal_cap=3200,
            cases=cases,
            responsibility_ratio=0.7,
            claimed_amount=6200,
        )
        self.assertFalse(result.magnitude_mismatch)
        self.assertEqual(result.case_median, 5425)
        self.assertIsNotNone(result.likely_high)

    def test_same_magnitude_large_samples_keep_interval(self):
        cases = [
            make_case(i, amount)
            for i, amount in enumerate([45000, 52000, 55000, 60000])
        ]
        result = build_assessment(
            expectation=200000,
            legal_cap=80000,
            cases=cases,
            responsibility_ratio=0.7,
            claimed_amount=80000,
        )
        self.assertFalse(result.magnitude_mismatch)
        self.assertIsNotNone(result.case_median)

    def test_no_amount_info_keeps_old_behavior(self):
        """连金额都没有时不启用闸门，保持原行为。"""
        cases = [make_case(i, amount) for i, amount in enumerate([5000, 5400, 5600])]
        result = build_assessment(
            expectation=0, legal_cap=0, cases=cases, responsibility_ratio=1.0
        )
        self.assertFalse(result.magnitude_mismatch)
        self.assertIsNotNone(result.case_median)

    def test_cross_bucket_samples_excluded_and_counted(self):
        cases = [
            make_case(1, 50000),
            make_case(2, 52000),
            make_case(3, 55000),
            make_case(4, 5000),
        ]
        result = build_assessment(
            expectation=200000,
            legal_cap=80000,
            cases=cases,
            responsibility_ratio=0.7,
            claimed_amount=80000,
        )
        self.assertFalse(result.magnitude_mismatch)
        self.assertEqual(result.magnitude_excluded, 1)


class MagnitudeGateEndToEndTest(unittest.TestCase):
    def test_real_injury_case_refuses_interval_with_reason(self):
        """用「8 万医疗费重伤」真实案情跑全流程：报告不得再给出 5,000 元级落点。"""
        profile = build_profile(
            "6月10日我父亲骑电动车被小轿车撞伤，交警认定对方主要责任。"
            "父亲住院治疗，医疗费已花 80000 元。",
            ["事故认定书.jpg", "医疗费发票.jpg"],
            region="浙江省",
        )
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="magnitude-e2e",
            role=Role.CLAIMANT,
            profile=profile,
            expectation=900000,
        )
        self.assertGreater(record.assessment.legal_cap, 20000, "8 万医疗费应能支撑大额可支持金额")
        self.assertTrue(record.assessment.magnitude_mismatch)
        body = next(a.body for a in record.artifacts if a.kind == "feasibility")
        self.assertIn("暂不提供", body)
        self.assertIn("量级", body)
        self.assertNotIn("5125", body.replace(",", ""))


if __name__ == "__main__":
    unittest.main()
