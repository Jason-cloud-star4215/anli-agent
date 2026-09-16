"""原告思路 vs 被告思路：两边不能写成同一个东西，金额一律用约数口径。"""

import unittest
from pathlib import Path

from anli.agents import STANCE_PLAYBOOK, AdvocacyAgent, AgentContext
from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


class StancePlaybookTest(unittest.TestCase):
    @staticmethod
    def make_agent(stance: Role) -> AdvocacyAgent:
        """只测规则版文案：跳过需要工具与事件总线的初始化。"""

        agent = AdvocacyAgent.__new__(AdvocacyAgent)
        agent.role = stance
        agent.display_name = "主张方" if stance == Role.CLAIMANT else "抗辩方"
        agent.playbook = STANCE_PLAYBOOK[stance]
        return agent

    @staticmethod
    def make_context() -> AgentContext:
        return AgentContext(
            task_id="stance",
            profile=car_accident_profile(),
            legal_cap=3200.0,
            expectation=car_accident_expectation(),
        )

    def test_two_sides_have_different_playbooks(self):
        claimant = STANCE_PLAYBOOK[Role.CLAIMANT]
        defendant = STANCE_PLAYBOOK[Role.DEFENDANT]
        self.assertNotEqual(claimant["sections"], defendant["sections"])
        self.assertNotEqual(claimant["goal"], defendant["goal"])
        self.assertEqual(
            list(claimant["sections"]), ["可主张项目", "举证顺序", "责任比例", "谈判锚点"]
        )
        self.assertEqual(
            list(defendant["sections"]), ["质证要点", "举证责任", "责任比例", "和解区间"]
        )

    def test_rule_fallback_keeps_the_two_thinking_styles(self):
        """没有大模型时也要能看出两边不是一个思路。"""

        context = self.make_context()
        claimant = self.make_agent(Role.CLAIMANT)
        defendant = self.make_agent(Role.DEFENDANT)

        claimant_bullets = claimant._rule_arguments(context, []).bullets
        defendant_bullets = defendant._rule_arguments(context, []).bullets

        self.assertTrue(any(item.startswith("【可主张项目】") for item in claimant_bullets))
        self.assertTrue(any(item.startswith("【谈判锚点】") for item in claimant_bullets))
        self.assertTrue(any(item.startswith("【质证要点】") for item in defendant_bullets))
        self.assertTrue(any(item.startswith("【和解区间】") for item in defendant_bullets))
        # 两边不能互相抄：原告不出现「和解区间」，被告不出现「举证顺序」
        self.assertFalse(any("和解区间" in item for item in claimant_bullets))
        self.assertFalse(any("举证顺序" in item for item in defendant_bullets))
        self.assertNotEqual(claimant_bullets, defendant_bullets)

    def test_amounts_are_hedged_with_approx_marker(self):
        agent = self.make_agent(Role.CLAIMANT)
        bullets = agent._rule_arguments(self.make_context(), []).bullets
        self.assertTrue(any("约" in item for item in bullets), bullets)


class SummaryApproxTest(unittest.TestCase):
    def run_task(self):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json", DATA / "cases.official.json"),
            state_store=StateStore(),
        )
        return orchestrator.run(
            task_id="approx",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )

    def test_summary_and_artifacts_use_approx_amounts(self):
        record = self.run_task()
        summary = record.extra.get("summary", "")
        self.assertIn("约", summary)
        self.assertIn("金额口径", summary)
        feasibility = next(item for item in record.artifacts if item.kind == "feasibility")
        self.assertIn("约", feasibility.body)
        self.assertIn("金额口径", feasibility.body)
        calculation = next(item for item in record.artifacts if item.kind == "calculation")
        self.assertIn("约数", calculation.body)


if __name__ == "__main__":
    unittest.main()
