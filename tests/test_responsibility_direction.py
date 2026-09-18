"""责任方向与「索赔没有责任基础」的处理。

踩过的坑：责任比例只认「程度」不认「谁的责任」，
于是「我负全部责任」被算成对方赔 100%——把责任判反了，是会说反话的错误。
"""

import unittest
from pathlib import Path

from anli.intake import build_profile, parse_responsibility_detail
from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def build() -> Orchestrator:
    return Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json", DATA / "cases.official.json"),
        state_store=StateStore(),
    )


class DirectionTest(unittest.TestCase):
    """比例统一表示「被告承担多少」，所以先要看清楚是谁的责任。"""

    CASES = [
        ("claimant", "交警认定我负全部责任，对方无责任。", 0.0),
        ("claimant", "交警认定我负主要责任，对方次要责任。", 0.3),
        ("claimant", "交警认定对方负主要责任。", 0.7),
        ("claimant", "对方全责。", 1.0),
        ("claimant", "交警认定双方同等责任。", 0.5),
        ("defendant", "交警认定我负主要责任。", 0.7),
        ("defendant", "交警认定对方（原告）负全部责任。", 0.0),
        ("defendant", "交警认定我无责任，对方全责。", 0.0),
        ("judge", "交警认定被告负主要责任。", 0.7),
        ("judge", "交警认定原告负全部责任。", 0.0),
    ]

    def test_direction_is_parsed_per_role(self):
        for role, text, expected in self.CASES:
            with self.subTest(role=role, text=text):
                ratio, reason = parse_responsibility_detail(text, "交通事故人身损害赔偿", role)
                self.assertEqual(ratio, expected)
                self.assertIn("被告", reason)

    def test_subject_nearest_to_the_keyword_wins(self):
        """「我要求对方承担全部责任」——责任是对方的，不能因为出现「我」就判反。"""

        ratio, _ = parse_responsibility_detail("我要求对方承担全部责任。", "", "claimant")
        self.assertEqual(ratio, 1.0)

    def test_no_responsibility_words_keeps_legacy_default(self):
        ratio, reason = parse_responsibility_detail("我们发生了交通事故。", "", "claimant")
        self.assertEqual(ratio, 1.0)
        self.assertEqual(reason, "")

    def test_non_liability_causes_ignore_the_ratio(self):
        ratio, reason = parse_responsibility_detail("商家欺诈", "消费维权", "claimant")
        self.assertEqual(ratio, 1.0)
        self.assertIn("不按责任比例", reason)


class NegationVariantsTest(unittest.TestCase):
    """否定说法的变体必须认识全。

    踩过的坑（Qoder 审计提示后复现）：「不承担任何责任」「无任何责任」这类写法
    在原词表里没有对应项，匹配不到 → 掉进默认值 1.0（对方全责）。
    原告侧侥幸看着对，**被告侧方向就是反的**——同一类会说反话的错误。
    """

    VARIANTS = [
        "交警认定我不承担任何责任。",
        "交警认定我不承担赔偿责任。",
        "交警认定我无任何责任。",
        "交警认定我不担责。",
        "交警认定我没有任何责任。",
    ]

    def test_defendant_negation_is_read_as_zero(self):
        for text in self.VARIANTS:
            with self.subTest(text=text):
                profile = build_profile(text, ["事故认定书"], "浙江省", role="defendant")
                self.assertEqual(profile.responsibility_ratio, 0.0, text)
                self.assertTrue(profile.extra["claim_has_no_basis"], text)

    def test_claimant_negation_puts_liability_on_the_other_side(self):
        for text in self.VARIANTS:
            with self.subTest(text=text):
                profile = build_profile(text, ["事故认定书"], "浙江省", role="claimant")
                self.assertEqual(profile.responsibility_ratio, 1.0, text)
                self.assertFalse(profile.extra["claim_has_no_basis"], text)

    def test_other_side_negation_removes_the_basis(self):
        profile = build_profile(
            "交警认定对方不应承担责任。", ["事故认定书"], "浙江省", role="claimant"
        )
        self.assertEqual(profile.responsibility_ratio, 0.0)
        self.assertTrue(profile.extra["claim_has_no_basis"])


class NoBasisFlagTest(unittest.TestCase):
    def test_claimant_at_fault_marks_no_basis(self):
        profile = build_profile(
            "我骑电动车撞了小轿车，交警认定我负全部责任，对方无责任。医疗费3200元。",
            ["事故认定书"],
            role="claimant",
        )
        self.assertEqual(profile.responsibility_ratio, 0.0)
        self.assertTrue(profile.extra["claim_has_no_basis"])
        self.assertIn("被告承担 0%", profile.extra["responsibility_basis"])

    def test_normal_case_has_no_flag(self):
        profile = build_profile(
            "我被小轿车撞了，交警认定对方负主要责任。医疗费3200元。",
            ["事故认定书"],
            role="claimant",
        )
        self.assertEqual(profile.responsibility_ratio, 0.7)
        self.assertFalse(profile.extra["claim_has_no_basis"])


class ClientMajorityTest(unittest.TestCase):
    """我方负主要责任（对方只占少数）时，结论必须先讲责任分摊。

    踩过的坑：用户写着「我主要责任」，结论却只说「证据还不充分，先别急着算能拿多少」，
    把决定上限的责任划分藏了起来。
    """

    @classmethod
    def setUpClass(cls):
        cls.orchestrator = build()
        cls.profile = build_profile(
            "我骑电动车被小轿车撞了，交警认定我负主要责任。医疗费3200元，误工费1800元，"
            "我想要对方赔12000元。",
            [],  # 故意不给材料：责任分摊仍要排在材料问题前面
            role="claimant",
        )
        cls.record = cls.orchestrator.run(
            task_id="client-majority",
            role=Role.CLAIMANT,
            profile=cls.profile,
            expectation=12000.0,
        )

    def body(self, kind: str) -> str:
        return next(item.body for item in self.record.artifacts if item.kind == kind)

    def test_share_and_flag(self):
        self.assertEqual(self.profile.responsibility_ratio, 0.3)
        self.assertEqual(self.profile.extra["client_share"], 0.7)
        self.assertTrue(self.profile.extra["client_bears_majority"])

    def test_conclusion_opens_with_the_ratio(self):
        first_line = self.body("summary").splitlines()[0]
        self.assertIn("责任分摊", first_line)
        self.assertIn("30%", first_line)
        self.assertNotIn("先别急着算能拿多少", first_line)

    def test_conclusion_explains_the_self_borne_share(self):
        summary = self.body("summary")
        self.assertIn("由你自己承担", summary)
        self.assertIn("不是把材料补齐就能改变的", summary)

    def test_first_strategy_is_about_the_ratio(self):
        strategy = self.body("strategy")
        self.assertIn("按责任比例折算", strategy)

    def test_equal_split_is_not_treated_as_majority(self):
        profile = build_profile(
            "交警认定双方同等责任。医疗费3200元。", [], role="claimant"
        )
        self.assertEqual(profile.responsibility_ratio, 0.5)
        self.assertFalse(profile.extra["client_bears_majority"])

    def test_defendant_side_is_told_responsibility_is_settled(self):
        profile = build_profile(
            "6月10号我开车右转蹭倒了骑电动车的人，交警出了事故认定书，认定我负主要责任。"
            "对方要求我赔偿医疗费3200元、误工费1800元，一共12000元。",
            [],
            role="defendant",
        )
        self.assertTrue(profile.extra["client_bears_majority"])
        record = self.orchestrator.run(
            task_id="client-majority-defendant",
            role=Role.DEFENDANT,
            profile=profile,
            expectation=12000.0,
        )
        first_line = next(
            item.body for item in record.artifacts if item.kind == "summary"
        ).splitlines()[0]
        self.assertIn("责任认定对你不利", first_line)
        strategy = next(item.body for item in record.artifacts if item.kind == "strategy")
        self.assertIn("把力气花在金额与证据上", strategy)


class HonestOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orchestrator = build()

    def run_case(self, description: str, role: Role, task_id: str):
        profile = build_profile(description, ["事故认定书", "医疗费票据"], role=role.value)
        record = self.orchestrator.run(
            task_id=task_id, role=role, profile=profile, expectation=12000.0
        )
        return profile, record

    def body(self, record, kind: str) -> str:
        return next(item.body for item in record.artifacts if item.kind == kind)

    def test_claimant_is_told_it_is_not_an_evidence_problem(self):
        _, record = self.run_case(
            "我骑电动车撞了小轿车，交警认定我负全部责任，对方无责任。我医疗费3200元，想要对方赔12000元。",
            Role.CLAIMANT,
            "honest-claimant",
        )
        summary = self.body(record, "summary")
        self.assertIn("对方不承担责任", summary)
        self.assertIn("复核", summary)
        self.assertNotIn("补齐后金额判断会更准", summary)

    def test_claimant_strategy_pivots_to_review_not_evidence(self):
        _, record = self.run_case(
            "我骑电动车撞了小轿车，交警认定我负全部责任，对方无责任。我医疗费3200元，想要对方赔12000元。",
            Role.CLAIMANT,
            "honest-claimant-strategy",
        )
        strategy = self.body(record, "strategy")
        self.assertIn("复核", strategy)
        self.assertIn("暂缓起诉", strategy)
        # 责任不成立时不该再教用户「补材料把金额抬上去」
        self.assertNotIn("把误工费计入损失", strategy)

    def test_claimant_document_carries_a_warning(self):
        _, record = self.run_case(
            "我骑电动车撞了小轿车，交警认定我负全部责任，对方无责任。我医疗费3200元，想要对方赔12000元。",
            Role.CLAIMANT,
            "honest-claimant-doc",
        )
        document = self.body(record, "document")
        self.assertIn("重要提示", document)
        self.assertIn("缺乏责任基础", document)

    def test_defendant_is_told_the_responsibility_finding_favours_them(self):
        _, record = self.run_case(
            "对方骑电动车撞了我的车，交警认定对方负全部责任。对方要求我赔医疗费3200元、误工费1800元。",
            Role.DEFENDANT,
            "honest-defendant",
        )
        summary = self.body(record, "summary")
        self.assertIn("责任认定对你有利", summary)
        self.assertNotIn("责任认定对你不利", summary)

    def test_normal_case_still_talks_about_amounts(self):
        record = self.orchestrator.run(
            task_id="honest-normal",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        summary = self.body(record, "summary")
        self.assertNotIn("缺乏责任基础", summary)


if __name__ == "__main__":
    unittest.main()
