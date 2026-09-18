"""《初赛方案》Markdown → Word（含插图/页码/目录）→ PDF。

用法：把初赛方案 Markdown 放到 01_初赛方案/ 下，再运行
    python tools/build_proposal_docx.py
输出 案理_初赛方案.docx 与 案理_初赛方案.pdf（Word COM 需要本机装有 Word）。
v3

v3 修订（按第二轮视觉复验）：
- 副标题行（## 参赛方案 —— …）不进目录；
- 目录样式压缩到一页内；
- 图片支持按宽度/高度上限自适应（竖长条截图按高度限制，不再放大发虚、不再截断）；
- 行内斜体与行内代码不再互相吞掉（附件清单里的反引号不会漏成字面量）；
- 段落间距压缩，避免末页孤行。
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

PKG = Path(r"C:\Users\wangzheng\Desktop\AI杭州模拟法庭")
SRC = PKG / "01_初赛方案" / "初赛方案_全文.md"
SHOTS = PKG / "02_演示素材" / "截图"
OUT_DOCX = PKG / "01_初赛方案" / "案理_初赛方案.docx"
OUT_PDF = PKG / "01_初赛方案" / "案理_初赛方案.pdf"

IMAGE_MAP = {
    "【截图 1": ("图1_整体三栏界面.png", "图 1　「案理」端到端界面：多智能体协同完成检索、对抗、质证、评估与交付的全流程视图", 14.5, None),
    "【截图 2": ("图8_验收证据文件.png", "图 2　自动化验收证据：441 项单元测试与 53 项端到端自检全部通过（对打包版实测）", 14.5, None),
    "【截图 3": ("图2_过程日志.png", "图 3　多智能体执行日志开头：任务受理 → 阶段推进 → 检索规划（模型决策与越界丢弃均留痕）", 14.5, None),
    "【截图 4": ("图3_引用核验_拼版.png", "图 4　引用核验面板（9 条引用拼接展示）：4 条法条经官方来源逐字核验并附链接，5 条类案如实标注为演示数据", 14.5, None),
    "【截图 5": ("图5_金额计算说明.png", "图 5　逐项认定与确定性计算清单：金额不由大模型生成，可复现、可审计", 14.5, None),
}
PATTERN = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")
SUBTITLE_PREFIXES = ("## 参赛方案",)


def add_runs(paragraph, text: str, italic_all: bool = False) -> None:
    pos = 0
    for match in PATTERN.finditer(text):
        if match.start() > pos:
            run = paragraph.add_run(text[pos : match.start()])
            run.italic = italic_all
        if match.group(1) is not None:
            run = paragraph.add_run(match.group(1)); run.bold = True
        else:
            run = paragraph.add_run(match.group(2))
            run.font.name = "Consolas"; run.font.size = Pt(9.5)
            run.font.color.rgb = RGBColor(0x8B, 0x2E, 0x2E)
        run.italic = run.italic or italic_all
        pos = match.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        run.italic = italic_all


def field(paragraph, instruction: str) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve"); instr.text = instruction
    end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
    run._r.append(begin); run._r.append(instr); run._r.append(end)


def add_page_numbers(document: Document) -> None:
    for section in document.sections:
        paragraph = section.footer.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run("第 "); run.font.size = Pt(9)
        field(paragraph, "PAGE")
        run = paragraph.add_run(" 页"); run.font.size = Pt(9)


def compact_toc_styles(document: Document) -> None:
    for name, size in (("TOC 1", 10), ("TOC 2", 9.5)):
        try:
            style = document.styles[name]
        except KeyError:
            continue
        style.font.size = Pt(size)
        style.paragraph_format.space_after = Pt(0)
        style.paragraph_format.space_before = Pt(0)
        style.paragraph_format.line_spacing = 1.0


def add_picture_sized(document: Document, image: Path, width_cm: float | None, max_height_cm: float | None) -> None:
    from PIL import Image

    with Image.open(image) as probe:
        px_w, px_h = probe.size
    ratio = px_h / px_w
    if width_cm is None and max_height_cm is not None:
        width_cm = max_height_cm / ratio
    assert width_cm is not None
    if max_height_cm is not None and width_cm * ratio > max_height_cm:
        width_cm = max_height_cm / ratio
    document.add_run().add_picture(str(image), width=Cm(width_cm))


def main() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines()
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    style.paragraph_format.space_after = Pt(4)
    style.paragraph_format.line_spacing = 1.12
    for section in document.sections:
        section.top_margin = section.bottom_margin = Cm(2.0)
        section.left_margin = section.right_margin = Cm(2.4)
    compact_toc_styles(document)

    for _ in range(6):
        document.add_paragraph()
    title = document.add_paragraph(); title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("「案理」· 人人可用的数字法律团队"); run.bold = True; run.font.size = Pt(26)
    sub = document.add_paragraph(); sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = sub.add_run("参赛方案 —— AI+超级智能体赛道：自主智能体，人人可用的数字员工")
    run.font.size = Pt(13); run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
    document.add_paragraph()
    info = document.add_paragraph(); info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info.add_run("参赛组别：高校组　|　团队成员：王铮、郑邦合　|　提交日期：2026.9.19")
    run.font.size = Pt(11)
    document.add_page_break()

    heading = document.add_paragraph()
    run = heading.add_run("目录"); run.bold = True; run.font.size = Pt(15)
    heading.paragraph_format.space_after = Pt(6)
    paragraph = document.add_paragraph()
    field(paragraph, r'TOC \o "1-2" \h \z \u')
    document.add_page_break()

    index = 0
    # 正文开头与封面重复的标题/副标题/信息行：跳过（封面已有）
    while index < len(lines):
        probe = lines[index].strip()
        if (
            probe.startswith("# ")
            or probe.startswith("## 参赛方案")
            or probe.startswith("> 参赛组别")
            or probe == ""
        ):
            index += 1
            continue
        break
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("【架构图"):
            image = SHOTS.parent / "01_初赛方案" / "系统架构图.drawio.png"
            if not image.exists():
                image = PKG / "01_初赛方案" / "系统架构图.drawio.png"
            if image.exists():
                picture = document.add_paragraph()
                picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
                add_picture_sized(picture, image, 15.5, None)
                picture.paragraph_format.keep_with_next = True
                caption_paragraph = document.add_paragraph()
                caption_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = caption_paragraph.add_run(
                    "系统架构图：四层结构 —— 用户输入 → 基础设施（23 条法条库 / 浙江标准表 / 类案库 / 证据材料）"
                    "→ 确定性引擎（逐项认定 → 金额计算 → 引用核验）→ Agent 博弈（主张方 → 抗辩方 → 方案修订 "
                    "→ 红队复核 → 评估官，按需追加第二轮，上限 2 轮）→ 交付物（13 项，证据不足时另加补证清单）"
                )
                run.font.size = Pt(9); run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
            index += 1
            continue

        if stripped.startswith("【截图"):
            for prefix, (filename, caption, width_cm, max_h) in IMAGE_MAP.items():
                if stripped.startswith(prefix):
                    image = SHOTS / filename
                    if image.exists():
                        picture = document.add_paragraph()
                        picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        add_picture_sized(document if False else picture, image, width_cm, max_h)
                        picture.paragraph_format.keep_with_next = True
                        caption_paragraph = document.add_paragraph()
                        caption_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        run = caption_paragraph.add_run(caption)
                        run.font.size = Pt(9); run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                    break
            index += 1
            continue

        if stripped in ("---", ">"):
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and set(lines[index + 1].strip()) <= set("|-: "):
            header = [cell.strip() for cell in stripped.strip("|").split("|")]
            rows: list[list[str]] = []
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                rows.append([cell.strip() for cell in lines[cursor].strip().strip("|").split("|")])
                cursor += 1
            table = document.add_table(rows=1, cols=len(header))
            table.style = "Light Grid Accent 1"
            header_tr = table.rows[0]._tr
            tr_pr = header_tr.get_or_add_trPr()
            tbl_header = OxmlElement("w:tblHeader")
            tbl_header.set(qn("w:val"), "true")
            tr_pr.append(tbl_header)
            tr_pr.append(OxmlElement("w:cantSplit"))
            for position, text in enumerate(header):
                cell = table.rows[0].cells[position]; cell.text = ""
                add_runs(cell.paragraphs[0], text)
                for run in cell.paragraphs[0].runs:
                    run.bold = True
                # 表头不能单独留在页尾：与第一条数据行绑定（Word 的 keep with next）
                cell.paragraphs[0].paragraph_format.keep_with_next = True
            for row in rows:
                added = table.add_row()
                added._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
                cells = added.cells
                for position, text in enumerate(row[: len(header)]):
                    cells[position].text = ""
                    add_runs(cells[position].paragraphs[0], text)
            index = cursor
            continue

        if stripped.startswith("```"):
            cursor = index + 1
            block: list[str] = []
            while cursor < len(lines) and not lines[cursor].strip().startswith("```"):
                block.append(lines[cursor]); cursor += 1
            paragraph = document.add_paragraph()
            run = paragraph.add_run("\n".join(block))
            run.font.name = "Consolas"; run.font.size = Pt(9)
            index = cursor + 1
            continue

        # 整行斜体：先剥外层单个星号，再处理行内加粗/代码
        italic_all = False
        text = stripped
        if text.startswith("*") and text.endswith("*") and not text.startswith("**") and len(text) > 2:
            text = text[1:-1]; italic_all = True

        if text.startswith("## ") and text.startswith(SUBTITLE_PREFIXES):
            paragraph = document.add_paragraph(); paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(text[3:]); run.font.size = Pt(12); run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
            paragraph.paragraph_format.keep_with_next = True
        elif text.startswith("### "):
            document.add_heading(text[4:], level=2).paragraph_format.keep_with_next = True
        elif text.startswith("## "):
            document.add_heading(text[3:], level=1).paragraph_format.keep_with_next = True
        elif text.startswith("# "):
            paragraph = document.add_paragraph()
            run = paragraph.add_run(text[2:]); run.bold = True; run.font.size = Pt(18)
            paragraph.paragraph_format.keep_with_next = True
        elif text.startswith("> "):
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(0.8)
            add_runs(paragraph, text[2:], italic_all=True)
        elif re.match(r"^[-*] ", text):
            paragraph = document.add_paragraph(style="List Bullet")
            add_runs(paragraph, text[2:])
        elif re.match(r"^\d+\. ", text):
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(0.75)
            paragraph.paragraph_format.first_line_indent = Cm(-0.75)
            add_runs(paragraph, text)
        elif text:
            paragraph = document.add_paragraph()
            add_runs(paragraph, text, italic_all=italic_all)
        index += 1

    add_page_numbers(document)
    document.save(str(OUT_DOCX))
    print("DOCX 已生成：", OUT_DOCX.stat().st_size, "字节")

    import win32com.client

    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        doc = word.Documents.Open(str(OUT_DOCX))
        # 目录页码要跟最终分页一致：先重排 + 更新一次，再更新一次后导出
        doc.Repaginate()
        doc.TablesOfContents(1).Update()
        doc.Repaginate()
        doc.TablesOfContents(1).Update()
        doc.SaveAs2(str(OUT_PDF), FileFormat=17)
        pages = doc.ComputeStatistics(2)
        doc.Close(False)
        print("PDF 已生成：", OUT_PDF.stat().st_size, "字节，共", pages, "页")
    finally:
        word.Quit()


if __name__ == "__main__":
    main()
