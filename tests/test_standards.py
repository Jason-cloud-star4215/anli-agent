"""法定赔偿标准表测试：数字必须对得上公开口径，查不到必须诚实回退。"""

import unittest
from pathlib import Path

from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.standards import StandardsStore, validate_record
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


class ValidateTest(unittest.TestCase):
    def test_bad_records_rejected(self):
        bad = {"key": "x", "value": -1, "unit": "元/天", "region": "浙江",
               "year": "今年", "official": "yes", "basis": "", "source_label": "",
               "source_url": "ftp://x"}
        issues = validate_record(bad)
        self.assertTrue(any("value" in issue for issue in issues))
        self.assertTrue(any("year" in issue for issue in issues))
        self.assertTrue(any("source_url" in issue for issue in issues))
        self.assertTrue(any("official" in issue for issue in issues))


class LookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = StandardsStore.load(DATA / "standards.json")

    def test_real_file_loads_without_problems(self):
        self.assertEqual(self.store.problems, [])
        self.assertGreaterEqual(len(self.store.records), 8)

    def test_lookup_takes_latest_data_year(self):
        record = self.store.lookup("private_unit_avg_wage")
        self.assertIsNotNone(record)
        self.assertEqual(record.year, 2024)
        self.assertEqual(record.value, 77230)

    def test_lookup_specific_year(self):
        record = self.store.lookup("urban_disposable_income", data_year=2023)
        self.assertEqual(record.value, 74997)

    def test_lookup_missing_key_returns_none(self):
        self.assertIsNone(self.store.lookup("不存在的标准"))
        self.assertEqual(StandardsStore().lookup("private_unit_avg_wage"), None)


class DescribeParamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = StandardsStore.load(DATA / "standards.json")

    def test_wage_param_gets_real_number_with_daily_conversion(self):
        text = self.store.describe_param("当地相同或相近行业上一年度职工平均工资")
        self.assertIn("77,230", text)
        self.assertIn("元/天", text)
        self.assertIn("浙高法审", text)

    def test_nutrition_param_gets_daily_standard(self):
        text = self.store.describe_param("当地营养费标准")
        self.assertIn("30 元/天", text)

    def test_user_fact_params_have_no_standard(self):
        self.assertEqual(self.store.describe_param("误工天数"), "")
        self.assertEqual(self.store.describe_param("伤残等级"), "")

    def test_empty_store_degrades_to_empty_string(self):
        self.assertEqual(StandardsStore().describe_param("当地营养费标准"), "")


class DisabilityCompensationTest(unittest.TestCase):
    """交叉验证：算出的金额必须与法院系统公布的赔偿表格一致。"""

    @classmethod
    def setUpClass(cls):
        cls.store = StandardsStore.load(DATA / "standards.json")

    def test_grade10_year2026_matches_published_table(self):
        result = self.store.disability_compensation(10, applicable_year=2026)
        self.assertEqual(result["amount"], 163298)  # 81649×20×10%，与公布表格一致
        self.assertEqual(result["formula"], "81,649 × 20 × 10% = 163,298 元")

    def test_grade10_year2025_matches_published_table(self):
        result = self.store.disability_compensation(10, applicable_year=2025)
        self.assertEqual(result["amount"], 156502)  # 78251×20×10%

    def test_grade_level_scales_index(self):
        # 五级伤残指数 60%：81649×20×0.6 = 979,788 元
        result = self.store.disability_compensation(5, applicable_year=2026)
        self.assertEqual(result["index"], 0.6)
        self.assertEqual(result["amount"], 979788)

    def test_unknown_year_degrades_honestly(self):
        self.assertIsNone(self.store.disability_compensation(10, applicable_year=2010))
        self.assertIsNone(StandardsStore().disability_compensation(10, applicable_year=2026))

    def test_invalid_grade_rejected(self):
        self.assertIsNone(self.store.disability_compensation(11, applicable_year=2026))


class OrchestratorIntegrationTest(unittest.TestCase):
    def test_calculation_artifact_shows_real_standards(self):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
                standards_store=StandardsStore.load(DATA / "standards.json"),
        )
        record = orchestrator.run(
            task_id="standards-car",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        calculation = next(
            item for item in record.artifacts if item.kind == "calculation"
        )
        self.assertIn("标准参考", calculation.body)
        self.assertIn("元/年", calculation.body)
        self.assertIn("浙高法审", calculation.body)

    def test_without_standards_store_keeps_old_behavior(self):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
        )
        record = orchestrator.run(
            task_id="standards-empty",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        calculation = next(
            item for item in record.artifacts if item.kind == "calculation"
        )
        self.assertNotIn("标准参考", calculation.body)


if __name__ == "__main__":
    unittest.main()
