"""范围闸门与转人工闸门：刑事案件不出结论，高风险先给事实再要确认。"""

import unittest
from pathlib import Path

from anli.intake import build_profile
from anli.models import Role, TaskState
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.scope import (
    detect_out_of_scope,
    human_review_reasons,
)
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"

CRIMINAL_TEXT = (
    "我朋友因为涉嫌盗窃罪被刑事拘留了，现在案子在检察院，我想知道能不能取保候审，"
    "会不会判刑。"
)
ADMIN_TEXT = "我对城管局的行政处罚决定不服，已经收到行政处罚决定书，想申请行政复议。"
CIVIL_TEXT = "我骑电动车被小轿车撞了，交警认定对方主要责任，医疗费花了3200元。"


def run(profile, expectation, approved=False, task_id="scope"):
    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json"),
        state_store=StateStore(),
    )
    events = []
    orchestrator.subscribe(events.append)
    record = orchestrator.run(
        task_id=task_id,
        role=Role.CLAIMANT,
        profile=profile,
        expectation=expectation,
        approved=approved,
    )
    return record, events


class ScopeDetectionTest(unittest.TestCase):
    def test_criminal_is_detected(self):
        scope = detect_out_of_scope(CRIMINAL_TEXT)
        self.assertIsNotNone(scope)
        self.assertEqual(scope.kind, "刑事")
        self.assertTrue(any("110" in item for item in scope.channels))

    def test_administrative_is_detected(self):
        scope = detect_out_of_scope(ADMIN_TEXT)
        self.assertIsNotNone(scope)
        self.assertEqual(scope.kind, "行政")
        self.assertTrue(any("行政复议" in item for item in scope.channels))

    def test_civil_is_not_blocked(self):
        self.assertIsNone(detect_out_of_scope(CIVIL_TEXT))

    def test_criminal_case_gets_no_analysis(self):
        """红线：刑事案件不出分析结论，也不给行动方案。"""

        profile = build_profile(CRIMINAL_TEXT)
        record, events = run(profile, 50000)
        self.assertEqual(record.state, TaskState.NEEDS_HUMAN)
        kinds = [item.kind for item in record.artifacts]
        self.assertEqual(kinds, ["out_of_scope"])
        body = record.artifacts[0].body
        self.assertIn("不在本产品的覆盖范围内", body)
        self.assertIn("12348", body)
        self.assertNotIn("策略建议", [item.title for item in record.artifacts])
        self.assertIn("task_completed", [event.type for event in events])
        self.assertEqual([event for event in events if event.type == "approval_required"], [])

    def test_criminal_case_cannot_be_approved(self):
        """即使声称已确认，也不能让刑事案件进入分析流程。"""

        profile = build_profile(CRIMINAL_TEXT)
        record, _ = run(profile, 50000, approved=True)
        self.assertEqual(record.state, TaskState.NEEDS_HUMAN)
        self.assertEqual([item.kind for item in record.artifacts], ["out_of_scope"])


class HumanReviewGateTest(unittest.TestCase):
    def test_reasons_cover_amount_limitation_and_safety(self):
        self.assertTrue(human_review_reasons(CIVIL_TEXT, 900_000))
        self.assertTrue(human_review_reasons(CIVIL_TEXT, 1000, limitation_days=30))
        self.assertTrue(human_review_reasons("对方一直威胁我，还动手打人", 1000))
        self.assertEqual(human_review_reasons(CIVIL_TEXT, 12_000, limitation_days=900), [])

    def test_high_amount_stops_before_action_plan(self):
        record, events = run(
            car_accident_profile(), expectation=900_000, task_id="human-gate"
        )
        self.assertEqual(record.state, TaskState.NEEDS_HUMAN)
        kinds = [item.kind for item in record.artifacts]
        self.assertIn("human_review", kinds)
        self.assertIn("feasibility", kinds)
        # 行动路径与文书草稿要等用户确认
        self.assertNotIn("strategy", kinds)
        self.assertNotIn("actions", kinds)
        self.assertNotIn("document", kinds)
        self.assertIn("approval_required", [event.type for event in events])
        self.assertTrue(record.extra.get("human_review"))
        self.assertTrue(record.extra.get("human_channels"))

    def test_approved_run_gets_full_delivery(self):
        record, _ = run(
            car_accident_profile(),
            expectation=900_000,
            approved=True,
            task_id="human-approved",
        )
        kinds = [item.kind for item in record.artifacts]
        self.assertIn("strategy", kinds)
        self.assertIn("actions", kinds)
        self.assertIn("document", kinds)
        self.assertIn(
            record.state, {TaskState.DELIVERED, TaskState.NEEDS_EVIDENCE}
        )

    def test_normal_case_is_not_interrupted(self):
        record, events = run(
            car_accident_profile(), expectation=car_accident_expectation(), task_id="normal"
        )
        self.assertEqual(record.state, TaskState.NEEDS_EVIDENCE)
        self.assertNotIn("approval_required", [event.type for event in events])


if __name__ == "__main__":
    unittest.main()
