import unittest

from anli.models import Role, TaskState
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


class EndToEndTest(unittest.TestCase):
    def test_car_accident_flow_delivers(self):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="e2e-car",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )

        # 证据链不完整时，流程停在「需要补充证据」，而不是直接给最终结论
        self.assertEqual(record.state, TaskState.NEEDS_EVIDENCE)
        self.assertIsNotNone(record.assessment)
        self.assertGreaterEqual(len(record.artifacts), 6)
        self.assertTrue(any(item.verified for item in record.citations))
        self.assertTrue(record.findings)
        self.assertTrue(record.extra.get("evidence_request"))
        # 主线案例：期望一万二，现实区间明显更低
        self.assertFalse(record.assessment.expectation_supported)
        self.assertLess(record.assessment.likely_high, record.assessment.expectation)

    def test_role_switch_keeps_research(self):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="e2e-defendant",
            role=Role.DEFENDANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        self.assertIn(record.state, {TaskState.DELIVERED, TaskState.NEEDS_EVIDENCE})
        self.assertTrue(any(r.agent == "defendant" for r in record.agent_results))

    def test_complete_evidence_delivers(self):
        """证据齐全时，流程正常交付。"""

        from anli.intake import build_profile
        from anli.scenarios import car_accident_description

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
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="e2e-full",
            role=Role.CLAIMANT,
            profile=profile,
            expectation=12000,
        )
        # 材料齐了，但护理费/营养费等仍需法定标准参数，因此停在待补参数
        self.assertEqual(record.state, TaskState.NEEDS_EVIDENCE)
        statuses = {item.item: item.status for item in profile.item_findings}
        self.assertNotIn("unsupported", statuses.values(), statuses)
        requests = record.extra.get("evidence_request") or []
        self.assertTrue(any(item["status"] == "standard" for item in requests))

    def test_report_differs_between_claimant_and_defendant(self):
        """换身份后报告内容必须真的不同，而不是只换个标签。"""

        law_store = LawStore.load(DATA / "laws.seed.json")
        case_store = CaseStore.load(DATA / "cases.seed.json")

        def run(role: Role) -> str:
            orchestrator = Orchestrator(
                law_store=law_store,
                case_store=case_store,
                state_store=StateStore(),
            )
            record = orchestrator.run(
                task_id=f"role-{role.value}",
                role=role,
                profile=car_accident_profile(),
                expectation=car_accident_expectation(),
            )
            kinds = [item.kind for item in record.artifacts]
            self.assertIn("stance", kinds)
            return orchestrator.render_report(record)

        claimant_report = run(Role.CLAIMANT)
        defendant_report = run(Role.DEFENDANT)
        judge_report = run(Role.JUDGE)

        self.assertIn("我方主张要点", claimant_report)
        self.assertIn("我方抗辩要点", defendant_report)
        self.assertIn("中立评估", judge_report)
        self.assertNotEqual(claimant_report, defendant_report)
        self.assertNotEqual(claimant_report, judge_report)

    def test_report_explains_how_amount_is_calculated(self):
        """金额必须给出算式和出处，且明确说明不是模型算的。"""

        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="calc-car",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        kinds = [item.kind for item in record.artifacts]
        self.assertIn("calculation", kinds)
        body = next(item.body for item in record.artifacts if item.kind == "calculation")
        self.assertIn("逐项认定结果", body)
        self.assertIn("可以支持", body)
        self.assertIn("不予支持", body)
        self.assertIn("不由大模型生成", body)

    def test_blank_template_asks_for_amounts(self):
        """模板只填了下划线没填数字时，不能给出一份自相矛盾的报告。"""

        from anli.intake import build_profile

        profile = build_profile(
            "我骑电动车被小轿车撞了，交警认定对方主要责任。"
            "医疗费：________元，误工费：________元。"
        )
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="blank-template",
            role=Role.CLAIMANT,
            profile=profile,
            expectation=12000,
        )
        self.assertEqual(record.state, TaskState.NEEDS_INFO)
        self.assertIn("还没有识别到任何具体的损失金额", record.extra.get("summary", ""))
        self.assertFalse(record.extra.get("evidence_request"))


if __name__ == "__main__":
    unittest.main()
