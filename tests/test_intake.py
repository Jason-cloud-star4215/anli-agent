import unittest

from anli.intake import build_profile, classify_cause, extract_amounts, extract_expectation
from anli.scenarios import (
    car_accident_description,
    car_accident_files,
    consumption_description,
    consumption_files,
)


class IntakeTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(
            classify_cause(car_accident_description()), "交通事故人身损害赔偿"
        )
        self.assertEqual(classify_cause(consumption_description()), "消费维权")

    def test_filled_template_parses_correctly(self):
        """用户按模板填空后，抽取结果必须干净可用。"""

        template = "\n".join(
            [
                "【事情经过】2026年6月10日，我在杭州市文一路路口，发生了电动车与小轿车碰撞。",
                "【责任认定】交警或平台认定：对方主要责任。",
                "【我的损失】",
                "医疗费：3200元",
                "误工费：1800元（误工15天）",
                "护理费：900元（护理30天）",
                "交通费：300元",
                "营养费：400元",
                "【手上的材料】我已经有：事故认定书、医疗费发票、误工证明、工资流水、交通费发票；"
                "暂时没有：护理费发票。",
                "【我希望的结果】要求对方赔偿12000元。",
            ]
        )
        profile = build_profile(template)
        self.assertEqual(profile.cause, "交通事故人身损害赔偿")
        self.assertEqual(profile.claim_items.get("医疗费"), 3200)
        self.assertEqual(profile.claim_items.get("误工费"), 1800)
        self.assertEqual(profile.claim_items.get("交通费"), 300)
        status = {item.item: item.status for item in profile.item_findings}
        self.assertEqual(status.get("护理费"), "unsupported")
        self.assertEqual(status.get("营养费"), "unsupported")
        self.assertAlmostEqual(profile.responsibility_ratio, 0.7)

    def test_sufficiency_is_zero_when_nothing_to_assess(self):
        """一项金额都没有时，材料齐备度不能显示成 100%。"""

        from anli.item_assessment import sufficiency

        self.assertEqual(sufficiency([]), 0.0)

    def test_amount_extraction_is_isolated_by_cause(self):
        """修过的 bug：消费维权的项目不能算进交通事故案。"""

        amounts = extract_amounts(car_accident_description(), "交通事故人身损害赔偿")
        self.assertIn("医疗费", amounts)
        self.assertNotIn("商品价款", amounts)
        self.assertNotIn("惩罚性赔偿", amounts)

    def test_uploaded_evidence_counts_fully_and_mention_only_partially(self):
        profile = build_profile(
            car_accident_description(), car_accident_files(), region="杭州"
        )
        # 医疗费有发票 → 按凭证全额认定；交通费只有口头说明 → 不予支持（归零）
        self.assertEqual(profile.claim_items["医疗费"], 3200)
        # 误工证明有、但没写明扣发金额且无流水 → 转按法定标准计算，暂不计入
        self.assertEqual(profile.blocked_items["误工费"]["kind"], "standard")
        self.assertNotIn("交通费", profile.claim_items)
        self.assertEqual(profile.blocked_items["交通费"]["kind"], "unsupported")

    def test_consumption_infers_triple_damages(self):
        profile = build_profile(consumption_description(), consumption_files())
        self.assertEqual(profile.cause, "消费维权")
        self.assertEqual(profile.claim_items["商品价款"], 2999)
        self.assertEqual(profile.claim_items["惩罚性赔偿"], 2999 * 3)

    def test_unrecognized_case_returns_generic_cause(self):
        profile = build_profile("你好，我想问一下这个事怎么办")
        self.assertEqual(profile.cause, "一般民事纠纷")
        self.assertEqual(profile.claim_items, {})

    def test_expectation_extraction(self):
        self.assertEqual(extract_expectation("我想要对方一共赔我12000元"), 12000)
        self.assertEqual(extract_expectation("希望赔偿 1.5 万元"), 15000)
        self.assertEqual(extract_expectation("没有提金额"), 0)

    def test_unquantified_item_is_detected(self):
        """提到了误工费却没给金额，必须提示补充，不能悄悄忽略。"""

        profile = build_profile("我骑电动车被撞了，医疗费花了3000元，误工费还没算。", [])
        self.assertIn("误工费", profile.extra["unquantified_items"])
        # 没有医疗费凭证 → 该项不予支持，而不是打折
        status = {item.item: item.status for item in profile.item_findings}
        self.assertEqual(status.get("医疗费"), "unsupported")

    def test_confirmed_evidence_full_but_details_change_path(self):
        """有材料就按材料认定；细节决定走哪条计算路径，而不是打折扣。"""

        rough = build_profile(
            "我骑电动车被小轿车撞了，医疗费花了3200元，误工费1800元。",
            [],
            confirmed_evidence=["医疗费票据", "误工证明"],
        )
        detailed = build_profile(
            "我骑电动车被小轿车撞了，医疗费花了3200元，误工费1800元。",
            [],
            confirmed_evidence=["医疗费票据", "误工证明"],
            quality_answers={
                "医疗费票据": ["formal", "amount", "related"],
                "误工证明": ["written", "period", "amount", "flow"],
            },
        )
        # 医疗费有票据 → 两种情况都按凭证全额认定
        self.assertEqual(rough.claim_items["医疗费"], 3200)
        self.assertEqual(detailed.claim_items["医疗费"], 3200)
        # 误工费：没写明扣发金额 → 只能按法定标准（暂不计入）；写明且能佐证 → 按实际收入全额
        self.assertNotIn("误工费", rough.claim_items)
        self.assertEqual(rough.blocked_items["误工费"]["kind"], "standard")
        self.assertEqual(detailed.claim_items["误工费"], 1800)

    def test_unknown_confirmed_name_ignored(self):
        profile = build_profile(
            "我骑电动车被小轿车撞了，医疗费花了3200元。",
            [],
            confirmed_evidence=["随便编的材料名"],
        )
        self.assertNotIn("随便编的材料名", profile.available_evidence)

    def test_evidence_detail_changes_amount(self):
        """细节决定认定路径：缺细节走法定标准，补齐后按实际收入。"""

        rough = build_profile(
            "我骑电动车被小轿车撞了，误工费1800元。",
            [],
            confirmed_evidence=["误工证明"],
        )
        detailed = build_profile(
            "我骑电动车被小轿车撞了，误工费1800元。",
            [],
            confirmed_evidence=["误工证明"],
            quality_answers={"误工证明": ["written", "amount"]},
        )
        self.assertNotIn("误工费", rough.claim_items)
        self.assertEqual(detailed.claim_items["误工费"], 1800)

    def test_quality_gap_produces_request(self):
        """有事实但缺凭证/参数时，要按法定标准列出还缺哪些参数。"""

        from anli.evidence_chain import build_evidence_requests

        profile = build_profile(
            "我骑电动车被小轿车撞了，误工费1800元。",
            [],
            confirmed_evidence=["误工证明"],
        )
        requests = build_evidence_requests(profile)
        standard_items = [item for item in requests if item.status == "standard"]
        self.assertTrue(standard_items, "缺凭证参数时应当生成按标准计算项")
        self.assertTrue(
            any("误工天数" in how for how in standard_items[0].how),
            standard_items[0].how,
        )


if __name__ == "__main__":
    unittest.main()
