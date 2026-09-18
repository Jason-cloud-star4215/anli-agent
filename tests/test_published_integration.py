"""官方发布案例接入「类案参照」的集成测试。

这一层最容易出的错是「越权」：把发布稿里的金额当成判决数据，混进金额区间。
所以这里除了验证「接上了」，更要验证「金额一步都没进计算」。
"""

import unittest
from pathlib import Path

from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.published_cases import PublishedCaseStore
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def build(published: bool = True) -> Orchestrator:
    return Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(
            DATA / "cases.seed.json",
            DATA / "cases.official.json",
            DATA / "real_cases.json",
        ),
        state_store=StateStore(),
        published_store=PublishedCaseStore.load(DATA / "cases.published.json")
        if published
        else None,
    )


def run(orchestrator: Orchestrator, task_id: str, role=Role.CLAIMANT):
    return orchestrator.run(
        task_id=task_id,
        role=role,
        profile=car_accident_profile(),
        expectation=car_accident_expectation(),
    )


def artifact_body(record, kind: str) -> str:
    return next((item.body for item in record.artifacts if item.kind == kind), "")


class StoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = PublishedCaseStore.load(DATA / "cases.published.json")

    def test_store_loads_and_filters_criminal(self):
        self.assertGreater(len(self.store), 50)
        for case in self.store.records:
            self.assertTrue(case.is_clean_civil, case.case_id)

    def test_search_only_returns_requested_cause(self):
        hits = self.store.search(
            ["交通事故人身损害赔偿", "误工费"], case_type="交通事故人身损害赔偿", limit=5
        )
        self.assertTrue(hits)
        for case in hits:
            self.assertEqual(case.cause, "交通事故人身损害赔偿")

    def test_empty_store_returns_nothing(self):
        self.assertEqual(PublishedCaseStore().search(["交通事故"], case_type="交通事故人身损害赔偿"), [])

    def test_rendered_lines_flag_amounts_as_reference_only(self):
        hits = self.store.search(["交通事故人身损害赔偿"], case_type="交通事故人身损害赔偿")
        lines = self.store.render_reference_lines(hits)
        text = "\n".join(lines)
        self.assertIn("来源：", text)
        if any(case.amounts for case in hits):
            self.assertIn("旁证，不参与区间计算", text)


class ReportIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.with_store = build(True)
        cls.without_store = build(False)
        cls.record = run(cls.with_store, "published-on")
        cls.plain = run(cls.without_store, "published-off")

    def test_research_section_lists_published_cases(self):
        body = artifact_body(self.record, "research")
        self.assertIn("官方发布案例参照", body)
        self.assertIn("非判决书全文", body)
        self.assertIn("gdcourts.gov.cn", body)

    def test_section_is_absent_when_no_cache_configured(self):
        self.assertNotIn("官方发布案例参照", artifact_body(self.plain, "research"))

    def test_published_amounts_never_enter_the_interval(self):
        """金额区间必须与「没有这份缓存」时一模一样。"""

        a, b = self.record.assessment, self.plain.assessment
        for field in ("legal_cap", "case_low", "case_median", "case_high",
                      "likely_low", "likely_high", "sample_size"):
            self.assertEqual(
                getattr(a, field), getattr(b, field), f"{field} 被发布案例金额影响了"
            )

    def test_published_amounts_do_not_appear_in_money_artifacts(self):
        published = self.record.extra.get("published_cases") or []
        amounts = {amount for case in published for amount in case.get("amounts") or []}
        self.assertTrue(amounts, "发布案例里应该有金额，否则这条测试没意义")
        guarded = " ".join(
            artifact_body(self.record, kind)
            for kind in ("summary", "feasibility", "calculation")
        )
        for amount in amounts:
            self.assertNotIn(
                amount, guarded, f"发布案例的金额 {amount} 出现在了金额类交付物里"
            )

    def test_research_section_marks_amounts_as_reference(self):
        body = artifact_body(self.record, "research")
        self.assertIn("金额只作旁证", body)

    def test_retrieval_events_carry_the_published_scope(self):
        events = []
        orchestrator = build(True)
        orchestrator.subscribe(events.append)
        run(orchestrator, "published-events")
        hits = [event for event in events if event.type == "retrieval_hit"]
        published = [event for event in hits if event.payload.get("scope") == "case_published"]
        self.assertTrue(published, "过程流里应当出现官方发布案例的命中记录")
        for event in published:
            self.assertTrue(event.payload.get("source", "").startswith("http"))

    def test_every_role_gets_the_reference_layer(self):
        for role in (Role.DEFENDANT, Role.JUDGE):
            record = run(self.with_store, f"published-{role.value}", role)
            self.assertIn("官方发布案例参照", artifact_body(record, "research"))


if __name__ == "__main__":
    unittest.main()
