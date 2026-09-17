"""交付物导出测试：导出的报告必须完整、一致、可直接打印发送。"""

import csv
import io
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from anli.exporter import (
    build_csv_export,
    build_docx_export,
    build_markdown_export,
    section_number,
)
from anli.models import Artifact, Role, TaskState
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def run_task(task_id: str):
    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json"),
        state_store=StateStore(),
    )
    record = orchestrator.run(
        task_id=task_id,
        role=Role.CLAIMANT,
        profile=car_accident_profile(),
        expectation=car_accident_expectation(),
    )
    return orchestrator, record


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orchestrator, cls.record = run_task("export-car")
        cls.markdown = build_markdown_export(cls.record, "export-car")

    def test_header_and_meta(self):
        self.assertIn("# 案理 · 法律分析报告", self.markdown)
        self.assertIn("报告编号 | export-car", self.markdown)
        self.assertIn("边界声明", self.markdown)
        self.assertIn("类案数据等级", self.markdown)

    def test_all_artifacts_present_with_titles(self):
        for artifact in self.record.artifacts:
            self.assertIn(artifact.title, self.markdown, f"导出缺少章节：{artifact.title}")

    def test_body_content_consistent_with_artifacts(self):
        summary = next(a for a in self.record.artifacts if a.kind == "summary")
        first_body_line = summary.body.split("\n")[0].strip()
        self.assertIn(first_body_line, self.markdown.replace("  \n", "\n"))

    def test_no_template_placeholders(self):
        self.assertNotIn("________", self.markdown, "不允许残留填空占位符")

    def test_footer_with_signature(self):
        self.assertIn("落款", self.markdown)
        self.assertIn("案理数字法律团队", self.markdown)

    def test_deferred_report_has_no_fake_content(self):
        """未完成任务（无交付物）导出时不能编造章节内容。"""

        empty_record = self.orchestrator.run(
            task_id="export-blank",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=0,
        )
        # 构造一个未完成记录：直接交付空 artifacts 的场景
        empty_record.artifacts = []
        markdown = build_markdown_export(empty_record, "export-blank")
        self.assertIn("# 案理 · 法律分析报告", markdown)
        self.assertNotIn("最可能落点", markdown.split("---")[-1])


class CsvExportTest(unittest.TestCase):
    """金额认定表：能被表格软件直接打开，且逐项结论与页面同源。"""

    @classmethod
    def setUpClass(cls):
        cls.orchestrator, cls.record = run_task("export-csv")
        cls.text = build_csv_export(cls.record, "export-csv")
        cls.rows = list(csv.reader(io.StringIO(cls.text)))

    def test_header_row_is_first(self):
        self.assertEqual(
            self.rows[0][:5],
            ["序号", "赔偿项目", "主张金额(元)", "认定状态", "认定金额(元)"],
        )

    def test_one_row_per_item_finding(self):
        data_rows = [row for row in self.rows[1:] if row and row[0].isdigit()]
        self.assertEqual(len(data_rows), len(self.record.profile.item_findings))

    def test_rows_use_the_same_status_labels_as_report(self):
        text = self.text
        self.assertTrue(
            any(label in text for label in ("可以支持", "需按法定标准计算", "可能不予支持")),
            "认定状态必须是中文标签，不能直接暴露 supported/standard/unsupported",
        )
        self.assertNotIn("unsupported,", text)

    def test_supported_rows_carry_amount(self):
        for row in self.rows[1:]:
            if len(row) >= 5 and row[3] == "可以支持":
                self.assertTrue(row[4].strip(), f"可以支持的项目必须给出金额：{row}")

    def test_carries_task_id_and_disclaimer(self):
        self.assertIn("export-csv", self.text)
        self.assertIn("不构成法律意见", self.text)
        self.assertIn("请人工复核", self.text)

    def test_no_placeholder(self):
        self.assertNotIn("________", self.text)


class DocxExportTest(unittest.TestCase):
    """Word 导出：手写 OOXML 必须真的是一个能被 Word 打开的包。"""

    @classmethod
    def setUpClass(cls):
        cls.orchestrator, cls.record = run_task("export-docx")
        cls.blob = build_docx_export(cls.record, "export-docx")
        cls.package = zipfile.ZipFile(io.BytesIO(cls.blob))
        cls.document = cls.package.read("word/document.xml").decode("utf-8")

    def test_package_has_minimal_required_parts(self):
        names = set(self.package.namelist())
        for part in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
            self.assertIn(part, names, f"缺少 OOXML 必需部件：{part}")

    def test_package_has_no_corrupted_member(self):
        self.assertIsNone(self.package.testzip())

    def test_document_xml_is_well_formed(self):
        root = ET.fromstring(self.document)
        self.assertTrue(root.tag.endswith("}document"), root.tag)
        self.assertIn("body", [child.tag.split("}")[-1] for child in root])

    def test_content_types_declares_document_part(self):
        content_types = self.package.read("[Content_Types].xml").decode("utf-8")
        self.assertIn("/word/document.xml", content_types)
        self.assertIn("wordprocessingml.document.main+xml", content_types)

    def test_every_artifact_title_present(self):
        for artifact in self.record.artifacts:
            self.assertIn(artifact.title, self.document, f"Word 导出缺少章节：{artifact.title}")

    def test_escaping_does_not_break_xml(self):
        """正文里出现 & < > 时也必须仍是合法 XML。"""

        record = self.orchestrator.run(
            task_id="export-escape",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        record.artifacts = [
            Artifact(kind="summary", title="A & B <C>", body="x < y & z > w"),
            Artifact(kind="analysis", title="引号\"与'撇号'", body="ok"),
        ]
        blob = build_docx_export(record, "export-escape")
        document = zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode("utf-8")
        ET.fromstring(document)  # 解析不通过会直接抛异常
        self.assertIn("A &amp; B &lt;C&gt;", document)
        self.assertIn("x &lt; y &amp; z &gt; w", document)

    def test_no_placeholder(self):
        self.assertNotIn("________", self.document)


class SectionNumberTest(unittest.TestCase):
    """交付物已超过 12 份，序号不能在第 13 份退化成阿拉伯数字。"""

    def test_section_number_continues_in_chinese(self):
        self.assertEqual(section_number(0), "一")
        self.assertEqual(section_number(11), "十二")
        self.assertEqual(section_number(12), "十三")
        self.assertEqual(section_number(19), "二十")

    def test_report_sections_are_numbered_consistently(self):
        orchestrator, record = run_task("export-number")
        report = orchestrator.render_report(record)
        tail_number = section_number(len(record.artifacts) - 1)
        self.assertIn(f"===== {tail_number}、", report)
        self.assertNotIn(f"===== {len(record.artifacts)}、", report)


if __name__ == "__main__":
    unittest.main()
