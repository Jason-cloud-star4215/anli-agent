import unittest

from anli.feasibility import build_assessment
from anli.models import CaseRecord


def make_case(index: int, amount: float) -> CaseRecord:
    return CaseRecord(
        case_id=f"demo-{index}",
        court="演示法院",
        year=2025,
        region="杭州",
        case_type="交通事故人身损害赔偿",
        dispute="赔偿金额",
        holding="法院按责任比例支持部分赔偿请求。",
        amounts={"合计": amount},
        synthetic=True,
    )


class FeasibilityTest(unittest.TestCase):
    def test_range_and_cap(self):
        cases = [make_case(i, amount) for i, amount in enumerate([5000, 5600, 6000, 6600, 7000])]
        result = build_assessment(
            expectation=12000, legal_cap=6600, cases=cases, responsibility_ratio=1.0
        )
        self.assertEqual(result.sample_size, 5)
        self.assertFalse(result.low_sample)
        self.assertEqual(result.case_median, 6000)
        self.assertLessEqual(result.likely_high, 6600)
        self.assertFalse(result.expectation_supported)
        self.assertTrue(any("上限" in reason for reason in result.gap_reasons))

    def test_low_sample_flagged(self):
        cases = [make_case(1, 6000)]
        result = build_assessment(
            expectation=8000, legal_cap=9000, cases=cases, responsibility_ratio=1.0
        )
        self.assertTrue(result.low_sample)
        self.assertTrue(any("样本不足" in reason for reason in result.gap_reasons))

    def test_no_case_at_all(self):
        result = build_assessment(
            expectation=8000, legal_cap=9000, cases=[], responsibility_ratio=1.0
        )
        self.assertEqual(result.sample_size, 0)
        self.assertIsNone(result.case_median)
        self.assertIsNone(result.likely_high)
        self.assertFalse(result.expectation_supported)

    def test_responsibility_ratio_applied(self):
        result = build_assessment(
            expectation=5000, legal_cap=10000, cases=[], responsibility_ratio=0.7
        )
        self.assertEqual(result.legal_cap, 7000)


if __name__ == "__main__":
    unittest.main()
