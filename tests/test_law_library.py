"""法条库真实性验收：消保法关键条文必须逐字核验入库且可检索、可引用。"""

import unittest
from pathlib import Path

from anli.models import Citation
from anli.stores import CaseStore, CitationVerifier, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


class ConsumerLawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.laws = LawStore.load(DATA / "laws.seed.json")
        cls.verifier = CitationVerifier(cls.laws, CaseStore.load(DATA / "cases.seed.json"))

    def test_article55_verified_with_full_text(self):
        article = self.laws.get("xbf-55")
        self.assertIsNotNone(article)
        self.assertTrue(article.verified, "第55条必须完成核验才能进入交付物")
        self.assertIn("三倍", article.text)
        self.assertIn("五百元", article.text)
        self.assertNotIn("摘要", article.text, "不允许残留摘要占位文本")
        self.assertTrue(article.source_url.startswith("http"))

    def test_related_articles_verified(self):
        for pid in ["xbf-08", "xbf-20", "xbf-23", "xbf-44", "xbf-49"]:
            article = self.laws.get(pid)
            self.assertIsNotNone(article, f"{pid} 缺失")
            self.assertTrue(article.verified, f"{pid} 未核验")

    def test_fraud_query_hits_article55_first(self):
        hits = self.laws.search(["欺诈赔偿"])
        self.assertTrue(hits)
        self.assertEqual(hits[0].provision_id, "xbf-55")
        self.assertTrue(hits[0].verified)

    def test_citation_from_article55_passes_verification(self):
        article = self.laws.get("xbf-55")
        citation = Citation(
            source_type="law",
            source_id="xbf-55",
            label=article.label,
            quote=article.text[:40],
        )
        result = self.verifier.verify(citation)
        self.assertTrue(result.verified)
        self.assertEqual(result.grade, "authoritative")

    def test_tampered_quote_still_rejected(self):
        citation = Citation(
            source_type="law",
            source_id="xbf-55",
            label="伪造引用",
            quote="这是编造的法条原文",
        )
        result = self.verifier.verify(citation)
        self.assertFalse(result.verified)

    def test_road_safety_law_76_verified(self):
        article = self.laws.get("ddja-76")
        self.assertIsNotNone(article)
        self.assertTrue(article.verified)
        self.assertIn("第三者责任强制保险", article.text)
        self.assertIn("百分之十", article.text)

    def test_arbitration_deadline_law_27_verified(self):
        article = self.laws.get("zcdf-27")
        self.assertIsNotNone(article)
        self.assertTrue(article.verified, "时效计算工具引用的就是这条，必须核验入库")
        self.assertIn("一年", article.text)

    def test_jiaoqiangxian_query_hits_road_law(self):
        hits = self.laws.search(["交强险"])
        self.assertTrue(hits)
        self.assertEqual(hits[0].provision_id, "ddja-76")


if __name__ == "__main__":
    unittest.main()
