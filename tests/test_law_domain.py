"""法条库分域：刑事条文可以入库，但绝不进民事检索与分析。"""

import unittest
from pathlib import Path

from anli.intake import build_profile
from anli.models import Role, TaskState
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_profile, car_accident_expectation
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"
CRIMINAL_TEXT = (
    "我表哥因为涉嫌诈骗罪被刑事拘留了，现在案子已经到检察院，家里想知道能不能取保候审、"
    "大概会判几年。"
)


def build_orchestrator():
    return Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json", DATA / "cases.official.json"),
        state_store=StateStore(),
    )


class LawDomainTest(unittest.TestCase):
    def setUp(self):
        self.store = LawStore.load(DATA / "laws.seed.json")

    def test_library_keeps_criminal_provisions(self):
        domains = {item.domain for item in self.store.provisions}
        self.assertIn("civil", domains)
        self.assertIn("criminal", domains)
        self.assertIn("administrative", domains)

    def test_administrative_provisions_are_not_citable_yet(self):
        """行政法条正文没核验，就不许被引用（宁可少说，不编条号）。"""

        admin = [item for item in self.store.provisions if item.domain == "administrative"]
        self.assertTrue(admin)
        self.assertTrue(all(not item.verified for item in admin))
        hits = self.store.search(["行政复议", "行政处罚"], limit=5, domain="administrative")
        self.assertEqual(hits, [], "未核验的条文不能进入检索结果")

    def test_administrative_provisions_never_reach_civil_search(self):
        hits = self.store.search(["行政复议", "行政处罚", "行政诉讼"], limit=10)
        # 关键是「搜到的都是民事条文」，而不是「搜不到任何东西」：
        # 例如《民法典》诉讼时效条文本身是民事条文，可以出现在结果里
        self.assertTrue(all(item.domain == "civil" for item in hits), hits)

    def test_criminal_text_is_verified_from_authoritative_source(self):
        for provision_id in ("xf-264", "xf-266"):
            provision = self.store.get(provision_id)
            self.assertIsNotNone(provision, provision_id)
            self.assertTrue(provision.verified, provision_id)
            self.assertEqual(provision.domain, "criminal")
            self.assertIn("spp.gov.cn", provision.source_url)
            # 正文必须是真条文，而不是「第X条的规定」这类引用句
            self.assertNotIn("的规定定罪处罚", provision.text[:30])

    def test_civil_search_never_returns_criminal_provisions(self):
        hits = self.store.search(["诈骗", "刑事", "盗窃", "判刑"], limit=10)
        self.assertTrue(all(item.domain == "civil" for item in hits), hits)

    def test_explicit_domain_search_finds_them(self):
        hits = self.store.search(["诈骗"], limit=3, domain="criminal")
        self.assertTrue(hits)
        self.assertTrue(all(item.domain == "criminal" for item in hits))
        self.assertEqual(hits[0].article, "第二百六十六条")

    def test_unrestricted_search_can_see_both(self):
        hits = self.store.search(["诈骗"], limit=5, domain=None)
        self.assertTrue(hits)


class OutOfScopeReferenceTest(unittest.TestCase):
    def test_notice_cites_criminal_provision_without_analyzing(self):
        record = build_orchestrator().run(
            task_id="scope-ref",
            role=Role.CLAIMANT,
            profile=build_profile(CRIMINAL_TEXT),
            expectation=0,
        )
        self.assertEqual(record.state, TaskState.NEEDS_HUMAN)
        body = record.artifacts[0].body
        self.assertIn("可能涉及的法律", body)
        self.assertIn("第二百六十六条", body)
        self.assertIn("本产品不对其做任何分析", body)
        # 仍然不给任何分析结论
        self.assertEqual([item.kind for item in record.artifacts], ["out_of_scope"])

    def test_view_hides_civil_analysis_for_criminal_cases(self):
        """超范围案件连「民事分析」字段都不能返回：不能出现案由/责任比例/争议焦点。"""

        import anli.web as web

        record = build_orchestrator().run(
            task_id="scope-view",
            role=Role.CLAIMANT,
            profile=build_profile(CRIMINAL_TEXT),
            expectation=0,
        )
        view = web.build_view(record, "报告")
        self.assertIsNone(view["understanding"])
        self.assertIsNone(view["assessment"])
        self.assertEqual(view["item_findings"], [])
        self.assertEqual(view["evidence_request"], [])
        self.assertEqual(view["strategies"], [])
        self.assertTrue(view["out_of_scope"])
        self.assertIn("覆盖范围", view["summary"])

    def test_civil_case_gets_no_criminal_citation(self):
        record = build_orchestrator().run(
            task_id="civil-clean",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        labels = [item.label for item in record.citations]
        self.assertTrue(labels)
        self.assertFalse(any("刑法" in label for label in labels))

    def test_administrative_notice_gives_law_names_not_article_numbers(self):
        """行政拦停：给法律名称与期限提示，但不编条号，也不做分析。"""

        record = build_orchestrator().run(
            task_id="scope-admin",
            role=Role.CLAIMANT,
            profile=build_profile(
                "我对城管局的行政处罚决定不服，已经收到行政处罚决定书，想申请行政复议。"
            ),
            expectation=0,
        )
        self.assertEqual(record.state, TaskState.NEEDS_HUMAN)
        body = record.artifacts[0].body
        self.assertIn("行政复议法", body)
        self.assertIn("行政处罚法", body)
        self.assertIn("具体条文以官方文本为准", body)
        self.assertIn("不对其做任何分析", body)
        self.assertNotIn("第", body.split("可能涉及的法律")[1][:20])


if __name__ == "__main__":
    unittest.main()
