"""证据闸门：证据不充分时不下结论性的赔付数字；责任比例只用于该用的案由。"""

import unittest
from pathlib import Path

from anli.intake import build_profile, responsibility_applies
from anli.llm_intake import profile_from_payload
from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import (
    car_accident_description,
    car_accident_expectation,
    car_accident_profile,
    consumption_description,
    consumption_expectation,
    consumption_profile,
)
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def run_task(profile, expectation, role=Role.CLAIMANT, task_id="gate"):
    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json", DATA / "cases.official.json"),
        state_store=StateStore(),
    )
    return orchestrator.run(
        task_id=task_id, role=role, profile=profile, expectation=expectation
    )


class ResponsibilityScopeTest(unittest.TestCase):
    def test_consumer_and_labour_do_not_use_ratio(self):
        self.assertFalse(responsibility_applies("消费维权"))
        self.assertFalse(responsibility_applies("劳动争议"))
        self.assertTrue(responsibility_applies("交通事故人身损害赔偿"))

    def test_llm_ratio_is_ignored_for_consumer_cases(self):
        """模型给消费维权编了个 50% 责任比例，必须被忽略（原来就是这么错的）。"""

        profile = profile_from_payload(
            {
                "cause": "消费维权",
                "summary": "买手机遇翻新机",
                "responsibility_ratio": 0.5,
                "claim_items": {"商品价款": 2999},
                "evidence": [{"name": "订单截图", "have_material": True}],
                "expectation": 11996,
            },
            consumption_description(),
            [],
        )
        self.assertEqual(profile.responsibility_ratio, 1.0)
        self.assertFalse(profile.extra.get("responsibility_applies"))

    def test_traffic_ratio_is_kept(self):
        profile = profile_from_payload(
            {
                "cause": "交通事故人身损害赔偿",
                "summary": "被撞",
                "responsibility_ratio": 0.7,
                "claim_items": {"医疗费": 3200},
                "evidence": [{"name": "医疗费票据", "have_material": True}],
                "expectation": 12000,
            },
            car_accident_description(),
            [],
        )
        self.assertEqual(profile.responsibility_ratio, 0.7)
        self.assertTrue(profile.extra.get("responsibility_applies"))


class EvidenceGateTest(unittest.TestCase):
    def test_insufficient_evidence_blocks_the_conclusion(self):
        record = run_task(car_accident_profile(), car_accident_expectation())
        self.assertIsNotNone(record.assessment)
        self.assertTrue(record.assessment.provisional, "缺误工/护理/交通/营养证据时应触发闸门")
        summary = record.extra.get("summary", "")
        self.assertIn("证据还不充分", summary)
        self.assertIn("不给结论性的可支持金额", summary)
        feasibility = next(
            item for item in record.artifacts if item.kind == "feasibility"
        )
        self.assertIn("结论状态：证据不充分", feasibility.body)
        self.assertIn("待补证", feasibility.body)
        self.assertIn("暂不定", feasibility.body)

    def test_defendant_wording_is_about_opponent_burden(self):
        record = run_task(
            car_accident_profile(), car_accident_expectation(), role=Role.DEFENDANT
        )
        summary = record.extra.get("summary", "")
        self.assertIn("对方举证还不充分", summary)
        self.assertIn("你暂不需要预估赔付", summary)

    def test_evidence_then_clears_the_gate(self):
        """补齐材料后应能给出结论性数字。"""

        profile = build_profile(
            car_accident_description(),
            [
                "事故认定书.jpg",
                "医疗费发票.jpg",
                "误工证明.jpg",
                "误工工资流水.pdf",
                "护理费发票.jpg",
                "交通费发票.jpg",
                "加强营养医嘱.jpg",
            ],
        )
        record = run_task(profile, car_accident_expectation(), task_id="gate-full")
        self.assertGreater(record.assessment.sufficiency, 0.7)
        self.assertFalse(record.assessment.provisional)
        feasibility = next(
            item for item in record.artifacts if item.kind == "feasibility"
        )
        self.assertIn("可给出结论性估算", feasibility.body)

    def test_consumer_case_never_says_responsibility_ratio(self):
        record = run_task(
            consumption_profile(), consumption_expectation(), task_id="gate-consumer"
        )
        analysis = next(item for item in record.artifacts if item.kind == "analysis")
        self.assertIn("责任比例：不适用", analysis.body)


if __name__ == "__main__":
    unittest.main()
