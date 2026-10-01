"""回归：blocked_items 的 missing 必须是「真缺失差集」，不是要件全集。

2026-09-19 主线运行的真实事故：用户明确说手上有误工证明，风险清单却写
「误工费因缺少 误工证明、工资流水 未计入」——红队复核当场指出矛盾，
但 finding 文案来自要件全集，修复后只列真正缺的那部分。
"""

import unittest

from anli.documents import _claim_lines, render_complaint
from anli.intake import build_profile, findings_to_profile_parts
from anli.item_assessment import assess_items
from anli.models import Role
from anli.scenarios import car_accident_description, car_accident_files


class MissingEvidenceDiffTest(unittest.TestCase):
    def setUp(self):
        self.profile = build_profile(
            car_accident_description(), car_accident_files(), region="浙江省"
        )

    def test_held_evidence_not_listed_as_missing(self):
        """误工证明已在手：误工费的 missing 只能是工资流水等真缺项。"""

        missing = self.profile.blocked_items["误工费"]["missing"]
        self.assertNotIn("误工证明", missing)
        # 描述写死「只有事故认定书、医疗费票据和误工证明三样」→ 工资流水真缺
        self.assertIn("工资流水", missing)

    def test_fully_missing_item_keeps_full_requirement(self):
        """护理费什么都没交：缺失清单保持完整要件，不因差集逻辑变短。"""

        missing = self.profile.blocked_items["护理费"]["missing"]
        self.assertIn("护理费票据", missing)
        self.assertIn("病历", missing)

    def test_findings_to_profile_parts_without_held_keeps_old_behavior(self):
        """不传 held（旧调用方式）时保持要件全集，兼容既有消费方。"""

        findings = assess_items(
            "交通事故人身损害赔偿",
            {"误工费": 1800},
            {"误工证明"},
            set(),
            {},
        )
        _, blocked, _, _ = findings_to_profile_parts(
            "交通事故人身损害赔偿", findings
        )
        self.assertIn("误工证明", blocked["误工费"]["missing"])
        self.assertIn("工资流水", blocked["误工费"]["missing"])

    def test_unsupported_finding_text_uses_true_missing_only(self):
        """风险提示文案不得把已持有的证据写成缺失。"""

        notes_text = str(self.profile.blocked_items)
        self.assertNotIn("缺少 误工证明", notes_text)


class ComplaintDetailTest(unittest.TestCase):
    def test_claim_detail_lists_all_claimed_items(self):
        """诉讼请求明细必须列全主张项目：合计 12,000 不能只配一条明细。"""

        profile = build_profile(
            car_accident_description(), car_accident_files(), region="浙江省"
        )
        lines = _claim_lines(profile)
        joined = "、".join(lines)
        self.assertIn("医疗费 3,200 元", joined)
        self.assertIn("误工费", joined)
        self.assertIn("护理费", joined)
        self.assertIn("待补证后另行主张", joined)

    def test_rendered_complaint_detail_is_complete(self):
        profile = build_profile(
            car_accident_description(), car_accident_files(), region="浙江省"
        )
        text = render_complaint(profile, None)
        self.assertIn("明细：", text)
        self.assertIn("待补证后另行主张", text)


if __name__ == "__main__":
    unittest.main()
