"""官方发布案例抓取工具测试：切分、字段提取与列表解析，全部离线。"""

import unittest

from tools.fetch_published_cases import (
    cut_cases,
    injury_of,
    list_items,
    responsibility_of,
    sources,
    to_text,
)

GUANGDONG_STYLE = """
广东法院机动车交通事故责任纠纷典型案例
一、侵权人赔付误工费不因受害人已获停工留薪待遇而减免
——项某诉刘某、黎某等机动车交通事故责任纠纷案
基本案情
黎某驾驶小客车违章停车，造成项某九级伤残的交通事故。交警部门认定刘某负主要责任，黎某负次要责任。
裁判结果
广东省高级人民法院经审理认为，领取停工留薪期间工资仍可向侵权人主张误工费。
典型意义
工伤保险待遇与民事侵权赔偿责任性质不同。
二、受害人退休后仍从事劳动可依法主张误工费
——韩某诉陈某、某安保险公司机动车交通事故责任纠纷案
基本案情
韩某与陈某负同等责任，法院酌定按 80% 认定。
裁判结果
判决保险公司赔偿 3.2 万元。
典型意义
退休不等于丧失劳动能力。
"""

HENAN_STYLE = """
案例1
机动车所有人对损害发生有过错的，应在过错范围内与机动车使用人共同承担赔偿责任
——李某与冯某、张某某、某保险公司机动车交通事故责任纠纷案
【基本案情】
冯某承担事故的全部责任，张某某应在过错范围内承担40%的赔偿责任。
【裁判结果】
判决某保险公司在交强险责任限额内赔偿李某32万余元。
【典型意义】
车辆所有人应加强对机动车的管理。
案例2
乘车人“开门杀”致人损害，保险公司和乘车人、驾驶人应依法赔偿
——潘某某与董某某、杜某某、某保险公司机动车交通事故责任纠纷案
【基本案情】
董某某、杜某某负同等责任。
【裁判结果】
判决保险公司赔偿潘某某32万余元。
【典型意义】
开车门前应谨慎注意。
"""


JIYUAN_STYLE = """
案例一
琚某诉程某、某保险公司 机动车交通事故责任纠纷案
——超过法定退休年龄的被侵权人的误工损失应当获得赔偿
【基本案情】2023年6月，程某驾车与琚某驾驶的摩托车相撞，交警认定双方同责。
【裁判结果】审理法院认为，误工费赔偿旨在填补实际收入减损，退休年龄非唯一标准，判决保险公司赔偿。
【典型意义】超龄劳动者的合法权益应当受到法律保护。
"""


class CaseNameOrderTest(unittest.TestCase):
    """有的发布稿把案名写在要旨前面，不能把两者写反。"""

    def test_name_first_layout(self):
        cases = cut_cases(JIYUAN_STYLE)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["case_name"], "琚某诉程某、某保险公司 机动车交通事故责任纠纷案")
        self.assertIn("超过法定退休年龄", cases[0]["title"])


class CutCasesTest(unittest.TestCase):
    def test_guangdong_style_with_numbered_headings(self):
        cases = cut_cases(GUANGDONG_STYLE)
        self.assertEqual(len(cases), 2)
        self.assertIn("停工留薪", cases[0]["title"])
        self.assertEqual(cases[0]["case_name"], "项某诉刘某、黎某等机动车交通事故责任纠纷案")
        self.assertIn("九级伤残", cases[0]["facts"])
        self.assertIn("误工费", cases[0]["holding"])
        self.assertIn("退休", cases[1]["title"])

    def test_henan_style_with_bracket_labels(self):
        cases = cut_cases(HENAN_STYLE)
        self.assertEqual(len(cases), 2)
        self.assertEqual(
            cases[0]["case_name"], "李某与冯某、张某某、某保险公司机动车交通事故责任纠纷案"
        )
        self.assertIn("全部责任", cases[0]["facts"])
        self.assertIn("32万余元", cases[0]["holding"])
        self.assertIn("典型意义", cases[0]["meaning"] + "典型意义")

    def test_article_without_cases_yields_nothing(self):
        self.assertEqual(cut_cases("这是一篇没有案子结构的新闻稿。"), [])


class FieldTest(unittest.TestCase):
    def test_responsibility_supports_ratio_and_named_levels(self):
        text = "交警认定冯某承担事故的全部责任；张某某应承担40%的赔偿责任；李某负次要责任。"
        value = responsibility_of(text)
        self.assertIn("全部责任", value)
        self.assertIn("40% 责任", value)
        self.assertIn("次要责任", value)

    def test_injury_level_is_normalised(self):
        self.assertEqual(injury_of("造成项某九级伤残的交通事故"), "九级伤残")
        self.assertEqual(injury_of("本案没有伤残鉴定"), "")


class ListItemTest(unittest.TestCase):
    def test_ids_and_titles_are_extracted(self):
        html = (
            '<a href="/gsxx/quanweifabu/anlihuicui/content/post_1842679.html">交通事故典型案例</a>'
            '<a href="/other/x.html">无关链接</a>'
        )
        self.assertEqual(
            list_items(html, r"content/post_(\d+)\.html"), [("1842679", "交通事故典型案例")]
        )


class SourceConfigTest(unittest.TestCase):
    def test_both_sources_configured(self):
        config = sources(gd_pages=3, hn_pages=2)
        self.assertEqual([item["key"] for item in config], ["gd_anli", "hn_typical"])
        self.assertEqual(len(config[0]["pages"]), 3)
        self.assertEqual(len(config[1]["pages"]), 2)
        self.assertTrue(config[1]["filter_titles"], "河南站必须按标题过滤，否则会抓进新闻与论文")

    def test_criminal_and_administrative_text_is_filtered(self):
        from tools.fetch_cases import excluded_from_library

        self.assertTrue(excluded_from_library("被告人犯交通肇事罪，判处有期徒刑"))
        self.assertTrue(excluded_from_library("本案属于行政处罚范围"))
        self.assertFalse(excluded_from_library("机动车交通事故责任纠纷，判决赔偿"))


class TextTest(unittest.TestCase):
    def test_html_is_flattened(self):
        text = to_text("<p>第一段</p><p>第二段&nbsp;内容</p>")
        self.assertIn("第一段", text)
        self.assertIn("第二段 内容", text)
        self.assertNotIn("<p>", text)


if __name__ == "__main__":
    unittest.main()
