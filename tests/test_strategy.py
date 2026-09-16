import unittest
from pathlib import Path

from anli.models import CaseProfile, FeasibilityAssessment, Role
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore
from anli.strategy import build_strategies, format_range, render_summary

DATA = Path(__file__).resolve().parent.parent / "data"


class StrategyTest(unittest.TestCase):
    def _run(self):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        return orchestrator.run(
            task_id="strategy-car",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )

    def test_report_starts_with_summary_and_strategy(self):
        record = self._run()
        self.assertIn("一句话结论", record.extra["summary"])
        self.assertEqual(record.artifacts[0].kind, "summary")
        self.assertIn("strategy", [item.kind for item in record.artifacts])
        self.assertTrue(record.extra["strategies"])

    def test_blocked_item_becomes_actionable_strategy(self):
        profile = CaseProfile(
            summary="测试",
            cause="交通事故人身损害赔偿",
            blocked_items={
                "误工费": {"amount": 1800.0, "missing": ["误工证明"], "strengthen": []}
            },
        )
        assessment = FeasibilityAssessment(
            expectation=12000,
            legal_cap=3000,
            case_low=3000,
            case_median=4000,
            case_high=5000,
            sample_size=5,
            low_sample=False,
            likely_low=3000,
            likely_high=3000,
        )
        strategies = build_strategies(profile, assessment, profile.blocked_items)
        actions = " ".join(item.action for item in strategies)
        self.assertIn("误工证明", actions)
        self.assertTrue(all(item.priority in {"P0", "P1", "P2"} for item in strategies))

    def test_range_format_handles_single_value(self):
        self.assertEqual(format_range(899, 899), "约 899")
        self.assertEqual(format_range(1000, 2000), "1000–2000")
        self.assertEqual(format_range(None, None), "无数据")

    def test_unquantified_item_becomes_first_step(self):
        profile = CaseProfile(
            summary="测试",
            cause="交通事故人身损害赔偿",
            extra={"unquantified_items": ["误工费"]},
        )
        assessment = FeasibilityAssessment(
            expectation=5000,
            legal_cap=5000,
            case_low=4000,
            case_median=5000,
            case_high=6000,
            sample_size=5,
            low_sample=False,
            likely_low=4000,
            likely_high=5000,
        )
        strategies = build_strategies(profile, assessment, {})
        self.assertIn("误工费", strategies[0].action)

    def test_strategies_differ_by_role(self):
        """同一份材料，三种身份的行动方案必须不一样。"""

        profile = car_accident_profile()
        assessment = FeasibilityAssessment(
            expectation=12000,
            legal_cap=6000,
            case_low=5000,
            case_median=5600,
            case_high=7000,
            sample_size=5,
            low_sample=False,
            likely_low=5000,
            likely_high=6000,
        )
        claimant = build_strategies(profile, assessment, {}, role=Role.CLAIMANT)
        defendant = build_strategies(profile, assessment, {}, role=Role.DEFENDANT)
        judge = build_strategies(profile, assessment, {}, role=Role.JUDGE)

        claimant_actions = [item.action for item in claimant]
        defendant_actions = [item.action for item in defendant]
        judge_actions = [item.action for item in judge]

        self.assertNotEqual(claimant_actions, defendant_actions)
        self.assertNotEqual(claimant_actions, judge_actions)
        self.assertTrue(any("保险" in action for action in claimant_actions))
        self.assertTrue(any("质证" in action or "要求对方" in action for action in defendant_actions))
        self.assertTrue(any("争点" in action or "举证" in action for action in judge_actions))

    def test_summary_wording_differs_by_role(self):
        profile = car_accident_profile()
        assessment = FeasibilityAssessment(
            expectation=12000,
            legal_cap=6000,
            case_low=5000,
            case_median=5600,
            case_high=7000,
            sample_size=5,
            low_sample=False,
            likely_low=5000,
            likely_high=6000,
        )
        claimant_summary = render_summary(profile, assessment, [], role=Role.CLAIMANT)
        defendant_summary = render_summary(profile, assessment, [], role=Role.DEFENDANT)
        judge_summary = render_summary(profile, assessment, [], role=Role.JUDGE)

        self.assertIn("你的诉求", claimant_summary)
        self.assertIn("对方主张", defendant_summary)
        self.assertIn("抗辩空间", defendant_summary)
        self.assertIn("本案", judge_summary)


if __name__ == "__main__":
    unittest.main()
