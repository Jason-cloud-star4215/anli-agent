"""数据等级与升级路径测试：区间不是实证样本时，报告必须说清怎么升级。"""

import unittest
from pathlib import Path

from anli.models import FeasibilityAssessment, Role
from anli.orchestrator import Orchestrator, sample_tier_line
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def make_assessment(sample_size: int, synthetic: int) -> FeasibilityAssessment:
    return FeasibilityAssessment(
        expectation=5000.0,
        legal_cap=4000.0,
        case_low=3000.0,
        case_median=3500.0,
        case_high=4000.0,
        sample_size=sample_size,
        low_sample=False,
        likely_low=3200.0,
        likely_high=3800.0,
        synthetic_sample_size=synthetic,
    )


class SampleTierLineTest(unittest.TestCase):
    def test_demo_samples_get_an_upgrade_path(self):
        line = sample_tier_line(make_assessment(sample_size=4, synthetic=4))
        self.assertIn("数据等级", line)
        self.assertIn("实证样本 0 件", line)
        self.assertIn("不足 3 件", line)
        self.assertIn("cases_cli.py import", line)
        self.assertIn("human_verified", line)
        self.assertIn("实证案例录入指南", line)

    def test_enough_real_samples_are_announced_as_empirical(self):
        line = sample_tier_line(make_assessment(sample_size=4, synthetic=0))
        self.assertIn("实证样本 4 件", line)
        self.assertNotIn("不足", line)
        self.assertNotIn("cases_cli", line)

    def test_mixed_samples_still_ask_for_more(self):
        line = sample_tier_line(make_assessment(sample_size=4, synthetic=2))
        self.assertIn("实证样本 2 件", line)
        self.assertIn("不足 3 件", line)


class ReportShowsTierTest(unittest.TestCase):
    def test_feasibility_artifact_carries_the_tier_line(self):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(
                DATA / "cases.seed.json",
                DATA / "cases.official.json",
                DATA / "real_cases.json",
            ),
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="sample-tier",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        feasibility = next(item for item in record.artifacts if item.kind == "feasibility")
        self.assertIn("数据等级：", feasibility.body)
        if record.assessment.synthetic_sample_size:
            self.assertIn("升级路径：", feasibility.body)


if __name__ == "__main__":
    unittest.main()
