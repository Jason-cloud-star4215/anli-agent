"""多轮对抗：复核意见 → 修订方案 → 对方再反驳 → 再次复核。

对应 PRD 第 5 节 P0-6「红队挑刺与修订（现名：质证复核）」：
主方案必须在设定的最大轮次内给出修订结果与变更说明。
"""

import unittest
from collections import Counter
from pathlib import Path

from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import (
    car_accident_expectation,
    car_accident_profile,
    consumption_expectation,
    consumption_profile,
)
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def run_task(profile, expectation, critic_rounds: int = 2, task_id: str = "rounds"):
    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json", DATA / "cases.official.json"),
        state_store=StateStore(),
    )
    orchestrator.budget.max_critic_rounds = critic_rounds
    events = []
    orchestrator.subscribe(events.append)
    record = orchestrator.run(
        task_id=task_id,
        role=Role.CLAIMANT,
        profile=profile,
        expectation=expectation,
    )
    return record, events


class MultiRoundTest(unittest.TestCase):
    def test_second_round_runs_when_findings_remain(self):
        record, events = run_task(car_accident_profile(), car_accident_expectation())
        rounds = [event for event in events if event.type == "round_started"]
        self.assertTrue(rounds, "有未解决的复核意见时必须进入第二轮")
        self.assertEqual(rounds[0].payload["round"], 2)
        self.assertGreater(rounds[0].payload["open_findings"], 0)
        self.assertTrue(record.extra.get("revisions"))

    def test_revision_artifact_explains_changes(self):
        record, _ = run_task(car_accident_profile(), car_accident_expectation())
        kinds = [item.kind for item in record.artifacts]
        self.assertIn("revision", kinds)
        body = next(item.body for item in record.artifacts if item.kind == "revision")
        self.assertIn("第 2 轮修订", body)
        self.assertIn("已修订", body)
        self.assertIn("仍未解决", body)

    def test_all_three_roles_speak_in_both_rounds(self):
        _, events = run_task(car_accident_profile(), car_accident_expectation())
        speakers = Counter(
            event.payload.get("agent")
            for event in events
            if event.type == "agent_delta"
        )
        self.assertGreaterEqual(speakers["claimant"], 4)
        self.assertGreaterEqual(speakers["defendant"], 4)
        self.assertGreaterEqual(speakers["redteam"], 2)

    def test_findings_are_deduplicated_and_partly_resolved(self):
        record, _ = run_task(car_accident_profile(), car_accident_expectation())
        details = [item.detail for item in record.findings]
        self.assertEqual(len(details), len(set(details)), "同一问题不能在多轮里重复计数")
        self.assertTrue(
            any(item.resolved for item in record.findings),
            "修订说明里标为已修订的问题，要在复核清单里体现出来",
        )

    def test_budget_can_limit_to_one_round(self):
        record, events = run_task(
            car_accident_profile(), car_accident_expectation(), critic_rounds=1
        )
        self.assertEqual([e for e in events if e.type == "round_started"], [])
        self.assertEqual(record.extra.get("revisions"), [])
        body = next(item.body for item in record.artifacts if item.kind == "revision")
        self.assertIn("未触发第二轮修订", body)

    def test_clean_case_does_not_force_extra_round(self):
        """没有中/高危问题时不硬凑轮次。"""

        record, events = run_task(
            consumption_profile(), consumption_expectation(), task_id="rounds-clean"
        )
        open_findings = [
            item
            for item in record.findings
            if not item.resolved and item.severity.value in {"high", "medium"}
        ]
        if not open_findings:
            self.assertEqual([e for e in events if e.type == "round_started"], [])


if __name__ == "__main__":
    unittest.main()
