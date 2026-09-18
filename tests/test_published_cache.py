"""官方发布案例缓存验收：字段齐全、来源可追溯、绝不混进刑事案例。

这份缓存是「类案参照」的文本层，与实证库（判决书全文）严格分开，
所以这里既查数据质量，也查它有没有把自己说成判决书。
"""

import json
import unittest
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "cases.published.json"

REQUIRED = (
    "case_id",
    "source_key",
    "source_name",
    "source_url",
    "fetched_at",
    "article_title",
    "title",
    "cause",
    "region",
    "amounts",
    "facts",
    "holding",
    "tier",
    "note",
)

CRIMINAL_MARKS = ("有期徒刑", "缓刑", "判处", "被告人", "公诉机关", "刑事拘留")


class PublishedCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = json.loads(DATA.read_text(encoding="utf-8"))

    def test_cache_is_not_empty(self):
        self.assertGreater(len(self.records), 50)

    def test_every_record_has_required_fields(self):
        for record in self.records:
            for field in REQUIRED:
                self.assertIn(field, record, f"{record.get('case_id')} 缺少字段 {field}")

    def test_every_record_is_traceable(self):
        for record in self.records:
            self.assertTrue(
                record["source_url"].startswith("http"), f"{record['case_id']} 来源链接不合法"
            )
            self.assertTrue(record["fetched_at"], f"{record['case_id']} 缺少抓取时间")
            self.assertTrue(record["title"].strip(), f"{record['case_id']} 没有标题")

    def test_records_are_labeled_as_published_not_judgments(self):
        for record in self.records:
            self.assertEqual(record["tier"], "published")
            self.assertIn("非判决书全文", record["note"])

    def test_no_criminal_cases_leak_in(self):
        for record in self.records:
            blob = record["title"] + record["facts"] + record["holding"]
            for mark in CRIMINAL_MARKS:
                self.assertNotIn(mark, blob, f"{record['case_id']} 混进了刑事表述：{mark}")

    def test_both_p0_causes_are_covered(self):
        causes = {record["cause"] for record in self.records}
        self.assertIn("交通事故人身损害赔偿", causes, "交安案例一条都没有，等于没扩到点子上")
        self.assertIn("消费维权", causes)

    def test_amount_ratio_is_meaningful(self):
        with_amount = [record for record in self.records if record["amounts"]]
        self.assertGreater(
            len(with_amount), len(self.records) // 2, "带金额的不到一半，弱参考价值有限"
        )

    def test_sources_are_official_court_sites(self):
        hosts = {record["source_url"].split("/")[2] for record in self.records}
        for host in hosts:
            self.assertTrue(
                host.endswith("court.gov.cn") or host.endswith("gdcourts.gov.cn"),
                f"来源不是法院官网：{host}",
            )


if __name__ == "__main__":
    unittest.main()
