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
        self.assertEqual(hits, [])

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


if __name__ == "__main__":
    unittest.main()
