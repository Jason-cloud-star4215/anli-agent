"""上传材料解析测试：能读的要真读到要素，读不到的要如实说读不到。"""

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from anli.intake import build_profile
from anli.llm_intake import build_profile_llm
from anli.materials import (
    MaterialFacts,
    cross_check_amounts,
    evidence_from_materials,
    parse_material,
    parse_materials,
    summarize,
)

CLAIM_DOC = """道路交通事故认定书
2024年5月3日，被告驾驶小轿车与原告电动车发生碰撞。
原告医疗费3200元，误工费1800元，护理费900元。
被告负主要责任。
随附材料：医疗费发票、病历、事故认定书。
"""


def _write_docx(path: Path, text: str) -> None:
    """构造一个最小可读的 docx。"""

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n"))
        + "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        package.writestr("word/document.xml", document)


def _write_pdf(path: Path, text: str) -> None:
    """构造一个内容流未压缩的 PDF（真实 PDF 多用 Flate，这里只为可测）。"""

    body = f"BT /F1 12 Tf ({text}) Tj ET".encode("utf-8")
    raw = (
        b"%PDF-1.4\n1 0 obj\n<< /Length "
        + str(len(body)).encode()
        + b" >>\nstream\n"
        + body
        + b"\nendstream\nendobj\ntrailer\n<< >>\n%%EOF\n"
    )
    path.write_bytes(raw)


class MaterialParseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _file(self, name: str, content: str, encoding: str = "utf-8") -> Path:
        path = self.dir / name
        path.write_text(content, encoding=encoding)
        return path

    def test_txt_extracts_amounts_dates_case_no_evidence(self):
        path = self._file("认定书.txt", CLAIM_DOC)
        facts = parse_material(path)
        self.assertTrue(facts.parsed)
        self.assertEqual(facts.kind, "文本")
        self.assertIn(3200.0, facts.amounts)
        self.assertIn("2024-05-03", facts.dates)
        self.assertIn("事故认定书", facts.evidence_tags)
        self.assertIn("医疗费票据", facts.evidence_tags)
        self.assertGreater(facts.text_chars, 50)

    def test_case_no_and_court_are_extracted(self):
        text = "浙江省杭州市西湖区人民法院 民事判决书 （2024）浙0108民初1234号 本院认为……"
        path = self._file("判决书.txt", text)
        facts = parse_material(path)
        self.assertEqual(facts.case_no, "（2024）浙0108民初1234号")
        self.assertIn("人民法院", facts.court)

    def test_gbk_file_is_readable(self):
        path = self._file("gbk材料.txt", "医疗费3200元，2024年5月3日就诊。", encoding="gbk")
        facts = parse_material(path)
        self.assertTrue(facts.parsed, facts.reason)
        self.assertIn(3200.0, facts.amounts)

    def test_docx_is_readable(self):
        path = self.dir / "起诉状.docx"
        _write_docx(path, "医疗费3200元\n误工费1800元")
        facts = parse_material(path)
        self.assertTrue(facts.parsed, facts.reason)
        self.assertEqual(facts.kind, "Word")
        self.assertIn(3200.0, facts.amounts)
        self.assertIn(1800.0, facts.amounts)

    def test_pdf_text_layer_is_readable(self):
        path = self.dir / "票据.pdf"
        _write_pdf(path, "医疗费3200元 2024年5月3日")
        facts = parse_material(path)
        self.assertTrue(facts.parsed, facts.reason)
        self.assertEqual(facts.kind, "PDF")
        self.assertIn(3200.0, facts.amounts)

    def test_pdf_without_text_layer_is_honestly_unparsed(self):
        path = self.dir / "扫描件.pdf"
        path.write_bytes(b"%PDF-1.4\nnothing here\n%%EOF\n")
        facts = parse_material(path)
        self.assertFalse(facts.parsed)
        self.assertIn("扫描件", facts.reason)

    def test_image_is_unparsed_with_ocr_reason(self):
        path = self.dir / "IMG_0001.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0fake")
        facts = parse_material(path)
        self.assertFalse(facts.parsed)
        self.assertEqual(facts.kind, "图片")
        self.assertIn("OCR", facts.reason)

    def test_unknown_format_is_unparsed_and_says_filename_only(self):
        path = self.dir / "表格.xlsx"
        path.write_bytes(b"PK\x03\x04fake")
        facts = parse_material(path)
        self.assertFalse(facts.parsed)
        self.assertIn("文件名", facts.reason)

    def test_corrupt_docx_does_not_raise(self):
        path = self.dir / "坏文件.docx"
        path.write_bytes(b"this is not a zip")
        facts = parse_material(path)
        self.assertFalse(facts.parsed)
        self.assertTrue(facts.reason)

    def test_empty_file_is_unparsed(self):
        path = self._file("空.txt", "   ")
        facts = parse_material(path)
        self.assertFalse(facts.parsed)
        self.assertIn("空", facts.reason)

    def test_excerpt_is_redacted(self):
        path = self._file("含隐私.txt", "我的手机号是13812345678，医疗费3200元。")
        facts = parse_material(path)
        self.assertNotIn("13812345678", facts.excerpt)
        self.assertIn("3200", facts.excerpt)


class MaterialAggregateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_summarize_counts_parsed_and_unparsed(self):
        good = self.dir / "a.txt"
        good.write_text(CLAIM_DOC, encoding="utf-8")
        image = self.dir / "b.jpg"
        image.write_bytes(b"\xff\xd8fake")
        facts = parse_materials([(good, "a.txt"), (image, "b.jpg")])
        summary = summarize(facts)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["parsed"], 1)
        self.assertEqual(summary["unparsed"], 1)
        self.assertIn("已解析 1 份", summary["text"])
        self.assertIn("未解析 1 份", summary["text"])

    def test_evidence_only_from_parsed_materials(self):
        good = self.dir / "a.txt"
        good.write_text(CLAIM_DOC, encoding="utf-8")
        image = self.dir / "b.jpg"
        image.write_bytes(b"\xff\xd8fake")
        facts = parse_materials([(good, "a.txt"), (image, "b.jpg")])
        tags = evidence_from_materials(facts)
        self.assertIn("医疗费票据", tags)

    def test_unparsed_material_contributes_nothing(self):
        image = self.dir / "b.jpg"
        image.write_bytes(b"\xff\xd8fake")
        facts = parse_materials([(image, "b.jpg")])
        self.assertEqual(evidence_from_materials(facts), set())

    def test_cross_check_reports_amounts_only_in_materials(self):
        good = self.dir / "a.txt"
        good.write_text("医疗费3200元，交通费600元。", encoding="utf-8")
        facts = parse_materials([(good, "a.txt")])
        notes = cross_check_amounts(facts, {"医疗费": 3200})
        self.assertTrue(notes)
        self.assertIn("600", notes[0])
        self.assertIn("请确认", notes[0])

    def test_cross_check_reports_amounts_only_in_description(self):
        good = self.dir / "a.txt"
        good.write_text("医疗费3200元。", encoding="utf-8")
        facts = parse_materials([(good, "a.txt")])
        notes = cross_check_amounts(facts, {"医疗费": 3200, "误工费": 1800})
        # 展示用千分位，所以断言带分隔符的写法
        self.assertTrue(any("1,800" in note for note in notes), notes)

    def test_cross_check_silent_when_no_material_text(self):
        facts = [MaterialFacts(name="x.jpg", kind="图片", parsed=False)]
        self.assertEqual(cross_check_amounts(facts, {"医疗费": 3200}), [])


class _FakeLLM:
    """记录提示词的假模型，用来验证「发出去的内容长什么样」。"""

    available = True
    calls = 0
    failures = 0

    def __init__(self, payload: dict | None = None):
        self.payload = payload or {}
        self.prompts: list[tuple[str, str]] = []

    def chat_json(self, system: str, user: str, **kwargs) -> dict:
        self.prompts.append((system, user))
        self.calls += 1
        return dict(self.payload)


CAR_DESC = (
    "我骑电动车被小轿车撞了，交警认定对方主要责任。"
    "医疗费3200元，误工费1800元。我的手机号是13812345678，身份证330106199001011234。"
)

# 描述里刻意不提任何证据名称：用来证明证据是「从材料正文读到的」，而不是关键词蒙对的
BARE_DESC = "我骑电动车被小轿车撞了，交警认定对方主要责任，我想要对方赔偿。"


class MaterialIntakeIntegrationTest(unittest.TestCase):
    """材料正文必须真的影响证据识别，且对外发送前必须脱敏。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _facts(self, text: str, name: str = "认定书.txt"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return parse_materials([(path, name)])

    def test_material_text_evidence_enters_profile(self):
        facts = self._facts("随附医疗费发票、病历、工资流水、事故认定书。医疗费3200元。")
        profile = build_profile(CAR_DESC, [], "浙江省", material_facts=facts)
        # 描述里提到过医疗费：来源要额外标明是材料正文里读到的
        self.assertIn("医疗费票据", profile.available_evidence)
        self.assertIn("材料正文中读到", profile.evidence_sources.get("医疗费票据", ""))
        # 描述里完全没提「工资流水」，它只可能来自材料正文
        self.assertIn("工资流水", profile.available_evidence)
        self.assertEqual(profile.evidence_sources.get("工资流水"), "材料正文中读到（已上传）")
        # 证据齐了，有凭证的项目才能进入可支持金额
        self.assertIn("医疗费", profile.claim_items or {})

    def test_unparsed_material_contributes_no_evidence(self):
        """图片读不出正文，就不能凭空多出一份证据。"""

        image = self.dir / "IMG.jpg"
        image.write_bytes(b"\xff\xd8fake")
        facts = parse_materials([(image, "IMG.jpg")])
        profile = build_profile(BARE_DESC, [], "浙江省", material_facts=facts)
        self.assertNotIn("医疗费票据", profile.available_evidence)
        self.assertNotIn("事故认定书", profile.available_evidence)

    def test_profile_records_material_notes(self):
        facts = self._facts("医疗费3200元，交通费600元。")
        profile = build_profile(CAR_DESC, [], "浙江省", material_facts=facts)
        notes = " ".join(profile.evidence_notes)
        self.assertIn("共 1 份材料", notes)
        self.assertIn("600", notes, "材料里有、描述里没有的金额要提示")

    def test_unparsed_reason_is_surfaced(self):
        image = self.dir / "IMG.jpg"
        image.write_bytes(b"\xff\xd8fake")
        facts = parse_materials([(image, "IMG.jpg")])
        profile = build_profile(CAR_DESC, [], "浙江省", material_facts=facts)
        notes = " ".join(profile.evidence_notes)
        self.assertIn("IMG.jpg", notes)
        self.assertIn("OCR", notes)

    def test_facts_are_kept_in_extra_for_reuse(self):
        facts = self._facts("医疗费3200元。")
        profile = build_profile(CAR_DESC, [], "浙江省", material_facts=facts)
        self.assertEqual(len(profile.extra["material_facts"]), 1)
        self.assertEqual(profile.extra["material_summary"]["parsed"], 1)

    def test_sensitive_text_is_redacted_before_sending(self):
        llm = _FakeLLM({"cause": "交通事故人身损害赔偿", "claim_items": {"医疗费": 3200}})
        profile = build_profile_llm(CAR_DESC, [], llm=llm)
        self.assertEqual(len(llm.prompts), 1)
        _, user_prompt = llm.prompts[0]
        self.assertNotIn("13812345678", user_prompt)
        self.assertNotIn("330106199001011234", user_prompt)
        self.assertIn("138****5678", user_prompt)
        self.assertIn("已打码", profile.extra["redaction_note"])
        self.assertFalse(profile.extra["sent_raw_text"])

    def test_report_keeps_user_words_while_payload_is_masked(self):
        llm = _FakeLLM({"cause": "交通事故人身损害赔偿", "claim_items": {"医疗费": 3200}})
        profile = build_profile_llm(CAR_DESC, [], llm=llm)
        self.assertIn("13812345678", profile.description, "本地产物保留用户原话")

    @unittest.mock.patch.dict("os.environ", {"ANLI_SEND_RAW_TEXT": "1"})
    def test_consent_switch_allows_raw_text(self):
        llm = _FakeLLM({"cause": "交通事故人身损害赔偿"})
        profile = build_profile_llm(CAR_DESC, [], llm=llm)
        _, user_prompt = llm.prompts[0]
        self.assertIn("13812345678", user_prompt)
        self.assertTrue(profile.extra["sent_raw_text"])

    def test_rules_path_needs_no_redaction(self):
        """没有模型时不对外发送，不需要脱敏，原话进本地产物。"""

        profile = build_profile_llm(CAR_DESC, [], llm=None)
        self.assertEqual(profile.intake_source, "rules")
        self.assertIn("13812345678", profile.description)


if __name__ == "__main__":
    unittest.main()
