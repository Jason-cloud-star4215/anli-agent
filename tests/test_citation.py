import unittest
from pathlib import Path

from anli.models import Citation, LegalProvision
from anli.stores import CaseStore, CitationVerifier, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


class CitationVerifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.laws = LawStore.load(DATA / "laws.seed.json")
        cls.cases = CaseStore.load(DATA / "cases.seed.json")
        cls.verifier = CitationVerifier(cls.laws, cls.cases)
        cls.fixture_laws = LawStore(
            [
                LegalProvision(
                    provision_id="fixture-1",
                    law_name="测试法",
                    article="第一条",
                    text="这是用于测试的条文原文。",
                    verified=True,
                )
            ]
        )
        cls.fixture_verifier = CitationVerifier(cls.fixture_laws, cls.cases)

    def test_verified_law_passes(self):
        provision = self.fixture_laws.provisions[0]
        citation = Citation(
            source_type="law",
            source_id=provision.provision_id,
            label=provision.label,
            quote=provision.text[:20],
        )
        result = self.fixture_verifier.verify(citation)
        self.assertTrue(result.verified)
        self.assertEqual(result.grade, "authoritative")

    def test_unverified_law_rejected(self):
        unverified_store = LawStore(
            [
                LegalProvision(
                    provision_id="fixture-2",
                    law_name="测试法",
                    article="第二条",
                    text="这是尚未核验的摘要。",
                    verified=False,
                )
            ]
        )
        verifier = CitationVerifier(unverified_store, self.cases)
        provision = unverified_store.provisions[0]
        citation = Citation(
            source_type="law",
            source_id=provision.provision_id,
            label=provision.label,
            quote=provision.text[:20],
        )
        result = verifier.verify(citation)
        self.assertFalse(result.verified)
        self.assertIn("核验", result.note)

    def test_tampered_quote_rejected(self):
        provision = self.fixture_laws.provisions[0]
        citation = Citation(
            source_type="law",
            source_id=provision.provision_id,
            label=provision.label,
            quote="这段文字并不存在于法条原文中",
        )
        result = self.fixture_verifier.verify(citation)
        self.assertFalse(result.verified)
        self.assertEqual(result.grade, "unverified")

    def test_unknown_case_rejected(self):
        citation = Citation(
            source_type="case",
            source_id="（2025）浙01民终99999号",
            label="不存在的案号",
            quote="任何内容",
        )
        result = self.verifier.verify(citation)
        self.assertFalse(result.verified)

    def test_synthetic_case_marked(self):
        case = next(item for item in self.cases.cases if item.synthetic)
        citation = Citation(
            source_type="case",
            source_id=case.case_id,
            label=case.label,
            quote=case.holding[:20],
        )
        result = self.verifier.verify(citation)
        self.assertTrue(result.verified)
        self.assertEqual(result.grade, "synthetic")


if __name__ == "__main__":
    unittest.main()
