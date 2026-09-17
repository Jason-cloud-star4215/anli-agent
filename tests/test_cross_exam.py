"""结构化质证测试：挑战必须落到项目、证据、异议类型与金额上。"""

import unittest

from anli.cross_exam import (
    build_challenges,
    render_challenges,
    total_at_stake,
)
from anli.item_assessment import ItemFinding
from anli.models import CaseProfile, Role
from anli.scenarios import car_accident_profile


class BuildChallengesTest(unittest.TestCase):
    def test_car_accident_generates_challenges(self):
        profile = car_accident_profile()
        challenges = build_challenges(profile)
        self.assertTrue(challenges, "主线案例存在缺证据项目，必须产生质证挑战")
        for challenge in challenges:
            self.assertTrue(challenge.target_item)
            self.assertIn(challenge.objection, {"no_evidence", "standard_required"})
            self.assertGreaterEqual(challenge.impact, 0)
            self.assertTrue(challenge.basis)
            self.assertTrue(challenge.if_upheld)

    def test_sorted_by_impact_desc(self):
        challenges = build_challenges(car_accident_profile())
        impacts = [challenge.impact for challenge in challenges]
        self.assertEqual(impacts, sorted(impacts, reverse=True))

    def test_supported_items_have_no_challenge(self):
        findings = [
            ItemFinding(
                item="医疗费", claimed=3200, status="supported", counted=3200,
                basis="有票据",
            )
        ]
        profile = CaseProfile(summary="测试", cause="交通事故人身损害赔偿")
        profile.item_findings = findings
        self.assertEqual(build_challenges(profile), [])

    def test_total_at_stake(self):
        profile = car_accident_profile()
        challenges = build_challenges(profile)
        self.assertEqual(
            total_at_stake(challenges),
            sum(challenge.impact for challenge in challenges),
        )


class RenderTest(unittest.TestCase):
    def test_render_differs_by_role(self):
        challenges = build_challenges(car_accident_profile())
        claimant = render_challenges(challenges, Role.CLAIMANT)
        defendant = render_challenges(challenges, Role.DEFENDANT)
        judge = render_challenges(challenges, Role.JUDGE)
        self.assertIn("攻击", claimant)
        self.assertIn("抗辩", defendant)
        self.assertIn("争点", judge)
        self.assertNotEqual(claimant, defendant)

    def test_render_empty_list(self):
        text = render_challenges([], Role.CLAIMANT)
        self.assertIn("没有明显的攻击空间", text)

    def test_render_contains_objection_label(self):
        challenges = build_challenges(car_accident_profile())
        text = render_challenges(challenges, Role.JUDGE)
        self.assertTrue(
            any(label in text for label in ("举证不能", "计算依据异议"))
        )


if __name__ == "__main__":
    unittest.main()
