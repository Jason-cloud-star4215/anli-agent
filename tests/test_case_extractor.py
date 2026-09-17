"""判决书半自动提取器测试：用合成判决书验证各字段解析与诚实降级。"""

import json
import unittest
from pathlib import Path

from tools.case_extractor import (
    extract_case_id,
    extract_court,
    extract_evidence_tags,
    extract_injury,
    extract_lost_wage,
    extract_ratio,
    extract_summary,
    extract_total,
    detect_case_type,
    extract_record,
    review_notes,
)

JUDGMENT = """浙江省杭州市某区人民法院
民事判决书

（2023）浙0105民初1234号

原告：张某。被告：王某。
本院查明：2022年5月，被告驾驶小轿车与原告电动车碰撞，交警认定被告承担主要责任。原告就医产生医疗费，提交了医疗费票据、事故认定书，并有单位出具的误工证明与工资流水。经鉴定，原告构成十级伤残。
本院认为：被告驾驶机动车未确保安全，承担主要责任，应赔偿原告各项损失。原告主张的医疗费有票据为凭，予以支持；误工费按实际减少的收入认定。

判决如下：一、被告赔偿原告医疗费8,000元、误工费4,500元、残疾赔偿金等共计132,000元；
二、驳回原告其他诉讼请求。
"""



class ExtractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = extract_record(JUDGMENT)

    def test_case_id_and_year(self):
        case_id, year = extract_case_id(JUDGMENT)
        self.assertEqual(case_id, "（2023）浙0105民初1234号")
        self.assertEqual(year, 2023)

    def test_court_and_region(self):
        self.assertIn("人民法院", extract_court(JUDGMENT))
        self.assertEqual(self.record["region"], "杭州")

    def test_injury_and_disability(self):
        level, disability_auth = extract_injury(JUDGMENT)
        self.assertEqual(level, "十级伤残")
        self.assertTrue(disability_auth)

    def test_ratio_mapping(self):
        ratio, basis = extract_ratio(JUDGMENT)
        self.assertEqual(ratio, 0.7)
        self.assertEqual(basis, "主要责任")

    def test_total_amount_from_judgment_section(self):
        total, candidates = extract_total(JUDGMENT)
        self.assertEqual(total, 132000)
        self.assertIn(8000, candidates)

    def test_evidence_tags_use_system_vocab(self):
        tags = extract_evidence_tags(JUDGMENT)
        self.assertIn("事故认定书", tags)
        self.assertIn("医疗费票据", tags)
        self.assertIn("误工证明", tags)

    def test_lost_wage_by_voucher(self):
        self.assertEqual(extract_lost_wage(JUDGMENT), "按凭证认定")

    def test_lost_wage_unsupported(self):
        text = "原告主张误工费2000元，但未能提供误工证明，本院不予支持。"
        self.assertEqual(extract_lost_wage(text), "未支持")

    def test_summary_from_benyuanrenwei(self):
        summary = extract_summary(JUDGMENT)
        self.assertTrue(summary.startswith("本院认为"))

    def test_case_type_detection(self):
        self.assertEqual(detect_case_type(JUDGMENT, ""), "交通事故人身损害赔偿")
        self.assertEqual(detect_case_type("商家虚假宣传构成欺诈，退一赔三", ""), "消费维权")
        self.assertEqual(detect_case_type("随便一段话", "消费维权"), "消费维权")

    def test_record_lands_as_pending(self):
        self.assertEqual(self.record["verification"], "imported_pending")
        self.assertGreater(self.record["total_amount"], 0)

    def test_review_notes_flag_human_items(self):
        notes = review_notes(self.record)
        self.assertTrue(any("来源链接" in note for note in notes), "没有来源链接必须提示人工补填")
        self.assertTrue(any("责任比例" in note for note in notes))


class HonestDegradationTest(unittest.TestCase):
    def test_garbage_text_yields_rejectable_draft(self):
        record = extract_record("这是一段完全没有判决要素的文字。")
        self.assertTrue(record["case_id"].startswith("待补案号"))
        self.assertEqual(record["total_amount"], 0)
        notes = review_notes(record)
        self.assertTrue(any("案号" in note for note in notes))
        self.assertTrue(any("判赔总额" in note for note in notes))
        # 校验器会拒绝这条草稿：金额为 0 不能入库
        from anli.real_cases import validate_record
        issues = validate_record(record)
        self.assertTrue(issues, "半成品草稿必须被校验器拦下，逼人工补齐")


if __name__ == "__main__":
    unittest.main()
