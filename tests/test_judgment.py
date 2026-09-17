"""模拟判决书与多轮修订测试（按合并后主干的双 Agent + 修订循环架构断言）。"""

import unittest
from pathlib import Path

from anli.models import Role, TaskState
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def make_orchestrator() -> Orchestrator:
    return Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(
            DATA / "cases.seed.json", DATA / "cases.official.json"
        ),
        state_store=StateStore(),
    )


def run(task_id: str):
    orchestrator = make_orchestrator()
    record = orchestrator.run(
        task_id=task_id,
        role=Role.CLAIMANT,
        profile=car_accident_profile(),
        expectation=car_accident_expectation(),
    )
    return orchestrator, record


class DebateStructureTest(unittest.TestCase):
    def test_debate_has_both_sides(self):
        _, record = run("debate-both")
        agents = [r.agent for r in record.agent_results]
        self.assertIn("claimant", agents)
        self.assertIn("defendant", agents)

    def test_multi_round_revision_loop_runs(self):
        """主线案例有未解决问题，修订循环应至少产生一轮修订说明。"""
        _, record = run("debate-revision")
        self.assertIsInstance(record.extra.get("revisions"), list)
        kinds = [a.kind for a in record.artifacts]
        self.assertIn("revision", kinds)

    def test_budget_not_exceeded(self):
        _, record = run("debate-budget")
        self.assertNotEqual(record.state, TaskState.FAILED)


class JudgmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orchestrator, cls.record = run("judgment-demo")

    def judgment_body(self):
        return next(a.body for a in self.record.artifacts if a.kind == "document")

    def test_document_is_mock_judgment(self):
        title = next(
            a.title for a in self.record.artifacts if a.kind == "document"
        )
        self.assertIn("模拟判决书", title)
        body = self.judgment_body()
        self.assertIn("不发生法律效力", body)
        self.assertIn("三、本院认为", body)
        self.assertIn("五、判决结果", body)

    def test_judgment_amount_matches_assessment(self):
        body = self.judgment_body()
        cap = self.record.assessment.legal_cap
        self.assertIn(f"{cap:,.0f} 元", body, "判决金额必须与评估的可支持金额一致")

    def test_judgment_has_case_reference_section(self):
        """「本院参考类案」段落必须存在：有 🟢/真实来源案例就引用，没有就诚实声明。"""
        body = self.judgment_body()
        self.assertIn("本院参考类案", body)


if __name__ == "__main__":
    unittest.main()
