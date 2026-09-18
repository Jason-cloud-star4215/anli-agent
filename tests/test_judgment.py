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

    def test_claimant_gets_a_party_document_not_a_judgment(self):
        """主张方拿到的是起诉状草稿，不能出现「本院认为」「判决结果」这类裁判口径。"""

        title = next(
            a.title for a in self.record.artifacts if a.kind == "document"
        )
        self.assertIn("起诉状", title)
        body = self.judgment_body()
        self.assertIn("一、诉讼请求", body)
        self.assertIn("三、证据清单", body)
        self.assertIn("不构成法律意见", body)
        self.assertNotIn("本院认为", body)
        self.assertNotIn("判决结果", body)
        self.assertNotIn("驳回原告", body)

    def test_party_document_amount_matches_assessment(self):
        body = self.judgment_body()
        cap = self.record.assessment.legal_cap
        self.assertIn(f"{cap:,.0f} 元", body, "文书里的金额必须与评估的可支持金额一致")

    def test_defendant_gets_an_answer_document(self):
        """抗辩方拿到的是答辩状：逐项质证 + 不出现裁判口径。"""

        from anli.documents import render_defense

        body = render_defense(self.record.profile, self.record.assessment)
        self.assertIn("民事答辩状（草稿）", body)
        self.assertIn("一、答辩意见", body)
        self.assertIn("四、答辩请求", body)
        self.assertNotIn("判决结果", body)
        self.assertNotIn("本院认为", body)

    def test_judge_gets_a_simulation_not_a_judgment(self):
        """法官视角拿到的推演草稿：措辞明确是推演，且只引真实来源类案。"""

        from anli.judgment import render_judgment

        body = render_judgment(self.record.profile, self.record.assessment, [])
        self.assertIn("不发生法律效力", body)
        self.assertIn("三、推演意见", body)
        self.assertIn("五、推演结论", body)
        self.assertIn("四、类案参照", body)
        self.assertIn("非审判组织、非裁判文书", body)
        self.assertNotIn("模拟审判组织", body)
        self.assertNotIn("判决结果", body)


if __name__ == "__main__":
    unittest.main()
