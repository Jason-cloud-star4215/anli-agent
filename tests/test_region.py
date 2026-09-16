"""地区口径：类案区间必须按用户所在省份取样本，并说清样本构成。"""

import unittest
from pathlib import Path

from anli.feasibility import build_assessment
from anli.models import CaseRecord
from anli.regions import DEFAULT_REGION, REGIONS, normalize_region
from anli.stores import CaseStore

DATA = Path(__file__).resolve().parent.parent / "data"


def case(case_id: str, region: str, amount: float, synthetic: bool = False) -> CaseRecord:
    return CaseRecord(
        case_id=case_id,
        court="演示法院（虚构）",
        year=2025,
        region=region,
        case_type="交通事故人身损害赔偿",
        dispute="赔偿范围",
        holding="示例裁判要旨",
        amounts={"医疗费": amount},
        synthetic=synthetic,
    )


class RegionAssessmentTest(unittest.TestCase):
    def test_local_samples_are_used_alone_when_enough(self):
        """本地区样本够用时，不能把外省案例混进区间。"""

        cases = [
            case("浙-1", "浙江省", 5000),
            case("浙-2", "浙江省", 6000),
            case("浙-3", "浙江省", 7000),
            case("苏-1", "江苏省", 99000),
        ]
        assessment = build_assessment(12000, 3000, cases, region="浙江省")
        self.assertEqual(assessment.sample_size, 3)
        self.assertEqual(assessment.region_sample_size, 3)
        self.assertEqual(assessment.cross_region_sample_size, 0)
        self.assertEqual(assessment.case_high, 7000)  # 外省 99000 不能进来
        self.assertIn("本地区（浙江省）3 件", assessment.sample_note)

    def test_falls_back_and_says_so_when_local_samples_are_few(self):
        """本地区样本不足时可以用外省样本，但必须在结论里说明。"""

        cases = [
            case("浙-1", "浙江省", 5000),
            case("苏-1", "江苏省", 6000),
            case("苏-2", "江苏省", 7000),
        ]
        assessment = build_assessment(12000, 3000, cases, region="浙江省")
        self.assertEqual(assessment.sample_size, 3)
        self.assertEqual(assessment.region_sample_size, 1)
        self.assertEqual(assessment.cross_region_sample_size, 2)
        self.assertTrue(
            any("本地区样本仅 1 件" in reason for reason in assessment.gap_reasons)
        )
        self.assertIn("其他省份 2 件", assessment.sample_note)

    def test_no_region_does_not_claim_cross_region(self):
        cases = [case("浙-1", "浙江省", 5000), case("苏-1", "江苏省", 6000)]
        assessment = build_assessment(12000, 3000, cases, region="")
        self.assertEqual(assessment.cross_region_sample_size, 0)
        self.assertEqual(assessment.sample_note, "样本 2 件")

    def test_demo_samples_are_counted_and_flagged(self):
        cases = [
            case("浙-1", "浙江省", 5000, synthetic=True),
            case("浙-2", "浙江省", 6000, synthetic=True),
            case("浙-3", "浙江省", 7000, synthetic=True),
        ]
        assessment = build_assessment(12000, 3000, cases, region="浙江省")
        self.assertEqual(assessment.synthetic_sample_size, 3)
        self.assertTrue(assessment.has_synthetic)
        self.assertIn("演示数据 3 件", assessment.sample_note)
        self.assertTrue(
            any("演示数据" in reason for reason in assessment.gap_reasons)
        )

    def test_seed_data_has_a_local_subset_for_the_main_demo(self):
        """主线示例的地区样本要够用，否则演示会退化成跨省混算。"""

        store = CaseStore.load(DATA / "cases.seed.json")
        cases = store.search_similar(case_type="交通事故人身损害赔偿", region=DEFAULT_REGION)
        local = [item for item in cases if item.region == DEFAULT_REGION]
        self.assertGreaterEqual(len(local), 3)

    def test_region_input_is_normalized(self):
        self.assertEqual(normalize_region("浙江"), "浙江省")
        self.assertEqual(normalize_region("浙江省"), "浙江省")
        self.assertEqual(normalize_region(""), DEFAULT_REGION)
        self.assertEqual(normalize_region("火星省"), DEFAULT_REGION)
        self.assertIn(DEFAULT_REGION, REGIONS)


if __name__ == "__main__":
    unittest.main()
