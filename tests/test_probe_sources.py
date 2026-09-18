"""数据源侦察工具测试：解析与报表要能离线复现，不依赖真的联网。"""

import unittest

from tools.probe_case_sources import render, source_list, title_hits


LIST_HTML = """
<a href="/gsxx/quanweifabu/anlihuicui/content/post_1842679.html">广东法院机动车交通事故责任纠纷典型案例</a>
<a href="/gsxx/quanweifabu/anlihuicui/content/post_1843835.html">广东法院2025年度商事金融十大案例</a>
<a href="/other/1.html">首页</a>
"""


class TitleHitTest(unittest.TestCase):
    def test_only_case_links_are_counted(self):
        hits = title_hits(LIST_HTML, r"content/post_(\d+)\.html")
        self.assertEqual(hits, ["广东法院机动车交通事故责任纠纷典型案例"])

    def test_short_or_irrelevant_titles_are_skipped(self):
        """标题太短、或跟民生侵权/消费无关的（如知识产权），都不算命中。"""

        html = (
            '<a href="/x/post_1.html">首页</a>'
            '<a href="/x/post_2.html">广东法院知识产权典型案例</a>'
            '<a href="/x/post_3.html">广东法院消费者权益司法保护典型案例</a>'
        )
        self.assertEqual(
            title_hits(html, r"post_(\d+)\.html"),
            ["广东法院消费者权益司法保护典型案例"],
        )


class SourceConfigTest(unittest.TestCase):
    def test_three_sources_with_required_fields(self):
        sources = source_list()
        self.assertEqual(
            [source["key"] for source in sources], ["hn_typical", "gd_anli", "fj_civil"]
        )
        for source in sources:
            for field in ("name", "kind", "pages", "item_re", "item_url"):
                self.assertIn(field, source)
            self.assertTrue(source["pages"])
            self.assertIn("{}", source["item_url"])


class RenderTest(unittest.TestCase):
    def test_report_lists_every_source(self):
        report = {
            "generated_at": "2026-09-17T00:00:00+00:00",
            "sources": [
                {
                    "key": "gd_anli",
                    "name": "广东法院网·权威发布·案例荟萃",
                    "kind": "省级法院权威发布",
                    "pages_ok": 2,
                    "item_ids": ["1", "2"],
                    "item_count": 2,
                    "title_hits": ["广东法院机动车交通事故责任纠纷典型案例"],
                    "title_hit_count": 1,
                    "samples": [
                        {
                            "url": "https://example.com/1",
                            "title": "样例",
                            "cause": "交通事故人身损害赔偿",
                            "amounts": ["2.1万元"],
                            "case_nos": [],
                            "is_judgment": False,
                        }
                    ],
                }
            ],
        }
        markdown = render(report)
        self.assertIn("| 广东法院网·权威发布·案例荟萃 |", markdown)
        self.assertIn("标题命中民生类关键词", markdown)
        self.assertIn("2.1万元", markdown)


if __name__ == "__main__":
    unittest.main()
