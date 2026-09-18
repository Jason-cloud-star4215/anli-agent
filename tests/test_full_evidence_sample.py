"""证据齐全样例：每一项主张都有材料时，流程要一路跑到结论。

主线车祸样例故意留了缺口（停在待补证），光看它看不出结论长什么样；
这个样例用来验证证据闸门放行后的效果，同时确认闸门本身没被顺带关掉。
"""

import unittest
from pathlib import Path

from anli.models import Role, TaskState
from anli.orchestrator import Orchestrator
from anli.published_cases import PublishedCaseStore
from anli.scenarios import (
    at_fault_expectation,
    at_fault_profile,
    car_accident_expectation,
    car_accident_profile,
    car_full_expectation,
    car_full_profile,
    consumption_expectation,
    consumption_gap_profile,
    consumption_profile,
)
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def build() -> Orchestrator:
    return Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(
            DATA / "cases.seed.json",
            DATA / "cases.official.json",
            DATA / "real_cases.json",
        ),
        state_store=StateStore(),
        published_store=PublishedCaseStore.load(DATA / "cases.published.json"),
    )


class FullEvidenceSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orchestrator = build()
        cls.profile = car_full_profile()
        cls.record = cls.orchestrator.run(
            task_id="full-evidence",
            role=Role.CLAIMANT,
            profile=cls.profile,
            expectation=car_full_expectation(),
        )

    def body(self, kind: str) -> str:
        return next(item.body for item in self.record.artifacts if item.kind == kind)

    def test_every_claim_item_is_supported(self):
        self.assertEqual(
            sorted(self.profile.claim_items), ["交通费", "医疗费", "护理费", "误工费"]
        )
        self.assertEqual(self.profile.blocked_items, {})
        self.assertEqual(self.profile.extra["sufficiency"], 1.0)

    def test_flow_reaches_delivery_with_a_conclusive_interval(self):
        self.assertEqual(self.record.state, TaskState.DELIVERED)
        assessment = self.record.assessment
        self.assertFalse(assessment.provisional)
        self.assertIsNotNone(assessment.case_low)
        self.assertIsNotNone(assessment.likely_high)
        self.assertGreater(assessment.legal_cap, 0)

    def test_summary_gives_a_conclusion_not_a_pending_note(self):
        summary = self.body("summary")
        self.assertIn("结论性估算", self.body("feasibility"))
        self.assertNotIn("先别急着算能拿多少", summary)
        self.assertNotIn("还有", summary.split("提醒：")[-1][:40] if "提醒：" in summary else "")

    def test_document_is_a_real_complaint_draft(self):
        document = self.body("document")
        self.assertIn("民事起诉状（草稿）", document)
        self.assertNotIn("重要提示", document)  # 责任成立，不该出现免责式的警告

    def test_gate_is_still_working_on_the_gap_sample(self):
        """闸门不能被这个样例顺手关掉：留缺口的样例仍应停在待补证。"""

        record = self.orchestrator.run(
            task_id="gap-sample",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        self.assertEqual(record.state, TaskState.NEEDS_EVIDENCE)
        self.assertTrue(record.assessment.provisional)


class SamplePairTest(unittest.TestCase):
    """示例按「证据不足 / 证据充足」成对，状态必须与按钮标注一致。

    按钮写着「证据充足」却停在待补证（或反过来），用户就分不清是产品坏了还是自己没填够。
    """

    CASES = [
        ("车祸·证据不足", car_accident_profile, car_accident_expectation, TaskState.NEEDS_EVIDENCE),
        ("车祸·证据充足", car_full_profile, car_full_expectation, TaskState.DELIVERED),
        (
            "消费·证据不足",
            consumption_gap_profile,
            consumption_expectation,
            TaskState.NEEDS_EVIDENCE,
        ),
        ("消费·证据充足", consumption_profile, consumption_expectation, TaskState.DELIVERED),
        ("责任在我（对方无责）", at_fault_profile, at_fault_expectation, TaskState.DELIVERED),
    ]

    @classmethod
    def setUpClass(cls):
        cls.orchestrator = build()

    def body(self, record, kind: str) -> str:
        return next(item.body for item in record.artifacts if item.kind == kind)

    def test_state_matches_the_button_label(self):
        for index, (name, profile_factory, expectation_factory, expected) in enumerate(self.CASES):
            with self.subTest(sample=name):
                record = self.orchestrator.run(
                    task_id=f"pair-{index}",
                    role=Role.CLAIMANT,
                    profile=profile_factory(),
                    expectation=expectation_factory(),
                )
                self.assertEqual(record.state, expected, f"{name} 的状态与标注不符")

    def test_gap_samples_say_what_is_missing(self):
        for index, (name, profile_factory, expectation_factory, expected) in enumerate(
            self.CASES
        ):
            if expected != TaskState.NEEDS_EVIDENCE:
                continue
            with self.subTest(sample=name):
                record = self.orchestrator.run(
                    task_id=f"pair-gap-{index}",
                    role=Role.CLAIMANT,
                    profile=profile_factory(),
                    expectation=expectation_factory(),
                )
                self.assertTrue(record.extra.get("evidence_request"), name)
                self.assertTrue(self.body(record, "evidence_request"))

    def test_at_fault_sample_is_delivered_with_the_honest_conclusion(self):
        record = self.orchestrator.run(
            task_id="pair-at-fault",
            role=Role.CLAIMANT,
            profile=at_fault_profile(),
            expectation=at_fault_expectation(),
        )
        summary = self.body(record, "summary")
        self.assertIn("缺乏责任基础", summary)
        self.assertFalse(record.extra.get("evidence_request"), "责任不成立就不该再要材料")


if __name__ == "__main__":
    unittest.main()
