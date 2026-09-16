"""「材料拿不出来，直接给结论」：点了就必须出结论，不能还挂着补证清单。"""

import unittest
from pathlib import Path

from anli.evidence_chain import build_evidence_requests
from anli.intake import build_profile
from anli.models import Role, TaskState
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_description, car_accident_files, car_accident_expectation
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def build_orchestrator():
    return Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json", DATA / "cases.official.json"),
        state_store=StateStore(),
    )


class WaiveAllTest(unittest.TestCase):
    def test_profile_records_the_flag(self):
        profile = build_profile(
            car_accident_description(), car_accident_files(), waive_all=True
        )
        self.assertTrue(profile.extra.get("waive_all"))
        self.assertEqual(build_evidence_requests(profile), [])

    def test_waive_all_leads_to_delivery_not_another_request_list(self):
        """这是用户点的那个按钮的核心承诺：点完要给结论，而不是再列一次清单。"""

        orchestrator = build_orchestrator()
        first = orchestrator.run(
            task_id="waive-1",
            role=Role.CLAIMANT,
            profile=build_profile(
                car_accident_description(), car_accident_files(), region="浙江省"
            ),
            expectation=car_accident_expectation(),
        )
        self.assertEqual(first.state, TaskState.NEEDS_EVIDENCE)
        self.assertTrue(first.extra.get("evidence_request"))

        second = orchestrator.run(
            task_id="waive-2",
            role=Role.CLAIMANT,
            profile=build_profile(
                car_accident_description(),
                car_accident_files(),
                region="浙江省",
                waive_all=True,
            ),
            expectation=car_accident_expectation(),
        )
        self.assertEqual(second.state, TaskState.DELIVERED)
        self.assertFalse(second.extra.get("evidence_request"))
        self.assertIn("你已说明这些材料暂时拿不出来", second.extra.get("summary", ""))

    def test_standard_items_cannot_be_waived_by_name(self):
        """误工天数、当地工资标准这类是计算参数而不是证据名称，只能靠 waive_all 放行。

        （这就是「点了没反应」的根因：按名字 waive 永远消不掉这类请求。）
        """

        profile = build_profile(
            car_accident_description(), car_accident_files(), region="浙江省"
        )
        requests = build_evidence_requests(profile)
        names = {name for item in requests for name in item.missing}
        self.assertTrue(
            any("误工" in name or "工资" in name for name in names),
            f"预期出现参数类缺口，实际：{names}",
        )
        # 只按这些名字 waive 是无效的：请求仍在
        partial = build_profile(
            car_accident_description(),
            car_accident_files(),
            region="浙江省",
            waived_evidence=sorted(names),
        )
        remaining = build_evidence_requests(partial)
        self.assertTrue(remaining, "参数类缺口不该被名字匹配消掉（这正是 waive_all 存在的理由）")


if __name__ == "__main__":
    unittest.main()
