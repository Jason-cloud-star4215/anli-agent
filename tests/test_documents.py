"""当事人文书测试：主张方拿起诉状、抗辩方拿答辩状，法官视角才拿推演。

三条红线（与 anli/documents.py 的模块说明一一对应）：
1. 当事人文书里不得出现「本院认为」「判决结果」「驳回原告」这类裁判口径；
2. 法院名称、案号、当事人姓名一律留占位符，不得编造；
3. 金额只能取自可行性评估，不得另编，也不得出现胜诉承诺。
"""

import unittest
from pathlib import Path

from anli.documents import DRAFT_HEAD, ROLE_NOTE, render_document
from anli.item_assessment import ItemFinding
from anli.models import CaseProfile, FeasibilityAssessment, Role

DATA = Path(__file__).resolve().parent.parent / "data"


def make_profile() -> CaseProfile:
    return CaseProfile(
        summary="两车相撞，对方全责，我方受伤。",
        cause="交通事故人身损害赔偿",
        description="2026年1月5日，被告驾驶小型轿车追尾原告所驾车辆，交警认定被告负全部责任。",
        region="杭州",
        responsibility_ratio=1.0,
        available_evidence=["医疗费票据", "事故认定书"],
        evidence_sources={"医疗费票据": "用户上传", "事故认定书": "用户确认已有"},
        claim_items={"医疗费": 1428.0, "误工费": 4000.0, "交通费": 300.0},
        blocked_items={"交通费": {"basis": "仅口头提及，无票据", "amount": 300.0}},
        item_findings=[
            ItemFinding("医疗费", 1428.0, "supported", 1428.0, "有医疗费票据，按收款凭证金额认定"),
            ItemFinding("误工费", 4000.0, "standard", 2450.0, "无收入证明，按行业标准计算"),
            ItemFinding("交通费", 300.0, "unsupported", 0.0, "无票据，举证不能"),
        ],
    )


def make_assessment() -> FeasibilityAssessment:
    return FeasibilityAssessment(
        expectation=5728.0,
        legal_cap=3878.0,
        case_low=3200.0,
        case_median=3600.0,
        case_high=4100.0,
        sample_size=4,
        low_sample=False,
        likely_low=3300.0,
        likely_high=3900.0,
        region="杭州",
    )


def body_of(role: Role) -> str:
    _, body = render_document(make_profile(), make_assessment(), role, [])
    return body


class DispatchTest(unittest.TestCase):
    def test_each_role_gets_its_own_document(self):
        profile, assessment = make_profile(), make_assessment()
        claimant_title, _ = render_document(profile, assessment, Role.CLAIMANT, [])
        defendant_title, _ = render_document(profile, assessment, Role.DEFENDANT, [])
        judge_title, _ = render_document(
            profile, assessment, Role.JUDGE, [], judgment_renderer=lambda *args: "推演正文"
        )
        self.assertIn("起诉状", claimant_title)
        self.assertIn("答辩状", defendant_title)
        self.assertIn("裁判推演", judge_title)
        self.assertNotIn("判决书", judge_title)

    def test_judge_role_delegates_to_judgment_renderer(self):
        title, body = render_document(
            make_profile(), make_assessment(), Role.JUDGE, [], judgment_renderer=lambda *args: "推演正文"
        )
        self.assertEqual(body, "推演正文")
        self.assertIn("模拟", title)


class ComplaintTest(unittest.TestCase):
    def test_structure(self):
        body = body_of(Role.CLAIMANT)
        self.assertIn(DRAFT_HEAD, body)
        for section in ("一、诉讼请求", "二、事实与理由", "三、证据清单", "四、此致"):
            self.assertIn(section, body)
        self.assertIn(ROLE_NOTE[Role.CLAIMANT], body)
        self.assertIn("具状人：", body)

    def test_no_judicial_wording(self):
        body = body_of(Role.CLAIMANT)
        for banned in ("本院认为", "判决结果", "驳回原告", "模拟审判组织", "胜诉", "保证能赢"):
            self.assertNotIn(banned, body)

    def test_amounts_match_assessment(self):
        body = body_of(Role.CLAIMANT)
        self.assertIn("3,878 元", body, "可支持金额必须取自评估结果")
        self.assertIn("5,728 元", body, "诉讼请求金额取自评估的期望值")


class DefenseTest(unittest.TestCase):
    def test_structure(self):
        body = body_of(Role.DEFENDANT)
        self.assertIn(DRAFT_HEAD, body)
        for section in ("一、答辩意见", "二、事实与理由", "三、对方举证情况", "四、答辩请求"):
            self.assertIn(section, body)
        self.assertIn(ROLE_NOTE[Role.DEFENDANT], body)

    def test_reuses_structured_challenges(self):
        """质证意见来自 build_challenges 的结构化结果，不是另写一段自由文本。"""

        body = body_of(Role.DEFENDANT)
        self.assertIn("关于", body)
        self.assertIn("涉及约", body)

    def test_no_judicial_wording_and_no_success_promise(self):
        body = body_of(Role.DEFENDANT)
        for banned in ("本院认为", "判决结果", "驳回原告", "必胜"):
            self.assertNotIn(banned, body)
        self.assertIn("不予支持", body)

    def test_amount_matches_assessment(self):
        self.assertIn("3,878 元", body_of(Role.DEFENDANT))

    def test_opinion_items_are_numbered_in_sequence(self):
        """质证条目与金额条目的编号必须连号（曾出现 1-4 之后接「2.」的断号）。"""

        body = body_of(Role.DEFENDANT)
        opinion = body.split("一、答辩意见", 1)[1].split("二、事实与理由", 1)[0]
        numbers = [
            int(line.split(".", 1)[0])
            for line in opinion.splitlines()
            if line[:1].isdigit() and "." in line
        ]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        self.assertGreaterEqual(len(numbers), 2)

    def test_arguments_end_with_punctuation(self):
        body = body_of(Role.DEFENDANT)
        for line in body.splitlines():
            if line.startswith("1. 关于") or line.startswith("2. 关于"):
                self.assertIn(line[-1], "。；！？", f"说理要点缺少句末标点：{line}")


class PlaceholderTest(unittest.TestCase):
    """不编造法院名称、案号与当事人信息——需要用户填的一律留占位符。"""

    def test_court_name_is_blank_placeholder(self):
        for role in (Role.CLAIMANT, Role.DEFENDANT):
            body = body_of(role)
            for line in body.splitlines():
                if "人民法院" in line:
                    self.assertTrue(
                        line.lstrip().startswith("_"),
                        f"法院名称必须是占位符而不是具体法院：{line}",
                    )

    def test_case_number_is_not_invented(self):
        body = body_of(Role.CLAIMANT)
        self.assertNotIn("民初", body)
        self.assertNotIn("号)", body)
        self.assertIn("请补全姓名／名称", body)

    def test_region_is_stated_as_reference_only(self):
        """地域只能是「本次分析采用的裁判尺度参照地」，不能写成管辖结论。"""

        body = body_of(Role.CLAIMANT)
        self.assertIn("参照地", body)
        self.assertIn("杭州", body)
        self.assertNotIn("本院管辖", body)


class DegradeTest(unittest.TestCase):
    """没有评估结果时不能写成「可支持金额约为 待定」。"""

    def test_missing_assessment_degrades_honestly(self):
        _, complaint = render_document(make_profile(), None, Role.CLAIMANT, [])
        self.assertNotIn("待定", complaint)
        self.assertIn("需在补齐材料后评估", complaint)
        _, defense = render_document(make_profile(), None, Role.DEFENDANT, [])
        self.assertNotIn("待定", defense)
        self.assertIn("尚需结合其举证逐项核定", defense)

    def test_no_blocked_items_still_reads_naturally(self):
        profile = make_profile()
        profile.blocked_items = {}
        _, complaint = render_document(profile, make_assessment(), Role.CLAIMANT, [])
        self.assertIn("暂未发现完全无凭证的项目", complaint)


class ReportOrderTest(unittest.TestCase):
    """文书草稿的优先级：紧跟策略建议，不能又被压回报告最后。"""

    @classmethod
    def setUpClass(cls):
        from anli.orchestrator import Orchestrator
        from anli.scenarios import car_accident_expectation, car_accident_profile
        from anli.state import StateStore
        from anli.stores import CaseStore, LawStore

        cls.orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(
                DATA / "cases.seed.json",
                DATA / "cases.official.json",
                DATA / "real_cases.json",
            ),
            state_store=StateStore(),
        )
        cls.record = cls.orchestrator.run(
            task_id="doc-order",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )

    def kinds(self) -> list[str]:
        return [artifact.kind for artifact in self.record.artifacts]

    def test_document_follows_strategy_immediately(self):
        kinds = self.kinds()
        self.assertIn("document", kinds)
        self.assertEqual(
            kinds.index("document"),
            kinds.index("strategy") + 1,
            "文书草稿必须紧跟策略建议，与它同级",
        )

    def test_document_is_in_the_top_four(self):
        kinds = self.kinds()
        self.assertLessEqual(
            kinds.index("document"), 3, "文书草稿要进报告前四节，不能压在最后"
        )

    def test_report_section_order_matches_artifacts(self):
        report = self.orchestrator.render_report(self.record)
        doc_index = report.index("民事起诉状（草稿）")
        strategy_index = report.index("策略建议（按优先级）")
        self.assertLess(doc_index, report.index("金额计算说明"))
        self.assertGreater(doc_index, strategy_index)

    def test_every_role_gets_its_document_in_the_same_slot(self):
        from anli.scenarios import car_accident_expectation, car_accident_profile

        for role in (Role.DEFENDANT, Role.JUDGE):
            record = self.orchestrator.run(
                task_id=f"doc-order-{role.value}",
                role=role,
                profile=car_accident_profile(),
                expectation=car_accident_expectation(),
            )
            kinds = [artifact.kind for artifact in record.artifacts]
            self.assertEqual(kinds.index("document"), kinds.index("strategy") + 1, role.value)


if __name__ == "__main__":
    unittest.main()
