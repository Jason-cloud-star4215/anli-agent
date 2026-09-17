"""Word 导出与检查点恢复导出测试。"""

import unittest
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path

from anli.docx_export import build_docx
from anli.exporter import (
    export_model_from_record,
    export_model_from_view,
    render_markdown,
)
from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def run_record(task_id: str = "docx-demo"):
    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(
            DATA / "cases.seed.json", DATA / "cases.official.json"
        ),
        state_store=StateStore(),
    )
    return orchestrator.run(
        task_id=task_id,
        role=Role.CLAIMANT,
        profile=car_accident_profile(),
        expectation=car_accident_expectation(),
    )


class DocxBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = run_record()
        cls.model = export_model_from_record(cls.record, "docx-demo")
        cls.payload = build_docx(cls.model)

    def test_is_valid_zip_with_required_parts(self):
        self.assertTrue(self.payload.startswith(b"PK"), "docx 必须是 zip 容器")
        with zipfile.ZipFile(BytesIO(self.payload)) as archive:
            names = set(archive.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("_rels/.rels", names)
            self.assertIn("word/document.xml", names)

    def test_document_xml_is_well_formed_and_contains_content(self):
        with zipfile.ZipFile(BytesIO(self.payload)) as archive:
            xml_bytes = archive.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        texts = "".join(node.text or "" for node in root.iter(f"{namespace}t"))
        self.assertIn("案理 · 法律分析报告", texts)
        self.assertIn("结论摘要", texts)
        self.assertIn("docx-demo", texts)
        self.assertIn("不构成法律意见", texts)

    def test_all_sections_present(self):
        with zipfile.ZipFile(BytesIO(self.payload)) as archive:
            xml_bytes = archive.read("word/document.xml")
        text = xml_bytes.decode("utf-8")
        for title, _ in self.model["sections"]:
            self.assertIn(title[:12], text, f"缺少章节：{title}")


class ViewFallbackTest(unittest.TestCase):
    """服务重启后：内存记录丢失，导出必须能从检查点视图重建。"""

    def test_markdown_from_view(self):
        view = {
            "role": "claimant",
            "report": "===== 一、结论摘要 =====\n一句话结论：测试。\n\n===== 二、诉求可行性评估 =====\n暂不提供。",
            "assessment": {"case_tier": "real"},
        }
        model = export_model_from_view(view, "restored-1")
        self.assertEqual(len(model["sections"]), 2)
        markdown = render_markdown(model)
        self.assertIn("结论摘要", markdown)
        self.assertIn("🟢 实证类案", markdown)
        payload = build_docx(model)
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            text = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("结论摘要", text)

    def test_empty_view_does_not_crash(self):
        model = export_model_from_view({}, "restored-2")
        payload = build_docx(model)
        self.assertTrue(payload.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
