"""后置否定证据回归：「票据还没找到」不得把未持有的证据计入可支持金额。

背景：冻结口径运行的用户原话末尾带了一句
「……护理费、交通费、营养费的票据都还没找到，工资流水也还没打。」
旧版抽取只看关键词前 8 字的否定词，落在后面的「还没找到」漏判，
把没找到的交通费票据当成有材料 → 可支持金额虚增至 2,450。
修复后必须回落到 2,240（仅医疗费 3,200 × 70%），且模型坚持
「有材料」时同样被否决。
"""

import unittest
from pathlib import Path

from anli.intake import (
    build_profile,
    explicitly_missing_evidence,
    extract_evidence,
)
from anli.llm_intake import profile_from_payload
from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"

BASE = (
    "6月10号我骑电动车被一辆小轿车撞了，交警出了事故认定书，对方主要责任。"
    "门诊治疗花了医疗费3200元，因为养伤误工费大概1800元，请护工花了护理费900元，"
    "打车去医院的交通费300元，医生说要补充营养，营养费400元。我想要对方一共赔我12000元。"
)
APPENDED = (
    "现在我手上只有事故认定书、医疗费票据和误工证明这三样，"
    "护理费、交通费、营养费的票据都还没找到，工资流水也还没打。"
)
FROZEN_TEXT = BASE + APPENDED
FILES = ["事故认定书.jpg", "医疗费发票.jpg", "误工证明.jpg"]


class ExtractEvidenceTest(unittest.TestCase):
    def test_post_negation_keeps_missing_tickets_out_of_strong(self):
        strong, weak = extract_evidence(
            "护理费、交通费、营养费的票据都还没找到，工资流水也还没打。"
        )
        for name in ("交通费票据", "护理费票据", "医嘱营养证明", "工资流水"):
            self.assertNotIn(name, strong, f"{name} 明说没找到，不得算有材料")
        self.assertIn("交通费票据", weak)

    def test_post_negation_does_not_kill_user_held_items(self):
        """同一句里的「只有…这三样」是肯定句式，不得被后置否定误伤。"""
        strong, _ = extract_evidence("我手上只有事故认定书、医疗费票据和误工证明这三样")
        self.assertIn("事故认定书", strong)
        self.assertIn("医疗费票据", strong)
        self.assertIn("误工证明", strong)

    def test_explicitly_missing_helper(self):
        missing = explicitly_missing_evidence("交通费的票据都还没找到，工资流水也还没打。")
        self.assertIn("交通费票据", missing)
        self.assertIn("工资流水", missing)
        self.assertEqual(explicitly_missing_evidence("我有医疗费发票和事故认定书"), set())


class LlmVetoTest(unittest.TestCase):
    def test_have_material_is_vetoed_by_user_text(self):
        """模型坚持「交通费票据有材料」，但原话说还没找到 → 必须被否决。"""
        payload = {
            "cause": "交通事故人身损害赔偿",
            "claim_items": {"医疗费": 3200, "交通费": 300},
            "evidence": [
                {"name": "交通费票据", "have_material": True},
                {"name": "医疗费票据", "have_material": True},
            ],
        }
        profile = profile_from_payload(payload, FROZEN_TEXT, FILES, region="浙江省")
        # 交通费票据被否决 → 来源降回「仅口头提及」，交通费落入 blocked/partial
        self.assertEqual(profile.evidence_sources.get("交通费票据"), "仅口头提及")
        self.assertTrue(
            "交通费" in profile.blocked_items or "交通费" in profile.partial_items,
            f"交通费应不计入可支持金额，实际 blocked={list(profile.blocked_items)} partial={list(profile.partial_items)}",
        )
        self.assertNotIn("交通费票据", profile.blocked_items)


class FrozenInputEndToEndTest(unittest.TestCase):
    def test_supported_amount_falls_back_to_2240(self):
        """含后置否定的冻结输入：可支持金额必须为 2,240（仅医疗费 3,200 × 70%）。"""
        profile = build_profile(FROZEN_TEXT, FILES, region="浙江省")
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="post-negation-e2e",
            role=Role.CLAIMANT,
            profile=profile,
            expectation=12000,
        )
        self.assertAlmostEqual(record.assessment.legal_cap, 2240.0, delta=1)
        self.assertLess(record.assessment.sufficiency, 0.65)
        source = build_profile(FROZEN_TEXT, FILES, region="浙江省").evidence_sources.get("交通费票据")
        self.assertIsNotNone(source)
        self.assertTrue(str(source).startswith("仅口头提及"), f"实际来源标注：{source}")


if __name__ == "__main__":
    unittest.main()
