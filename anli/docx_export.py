"""Word (.docx) 导出：纯标准库手写最小 OOXML，不引入任何第三方依赖。

.docx 本质是一个 zip：三条必需件即可被 Word / WPS 正常打开：
- [Content_Types].xml
- _rels/.rels
- word/document.xml

排版从简（标题蓝色加粗、正文引号缩进），保证「打开即正常、打印即像样」，
不追求样式体系的完整还原。
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

BRAND = "1F4E79"
BODY_SIZE = 21   # 半磅：10.5pt（五号）
H1_SIZE = 36
H2_SIZE = 28
META_SIZE = 19

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _paragraph(text: str, bold: bool = False, size: int = BODY_SIZE, color: str = "", align: str = "", after: int = 120) -> str:
    run_props = "<w:rPr>"
    if bold:
        run_props += "<w:b/>"
    run_props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    if color:
        run_props += f'<w:color w:val="{color}"/>'
    run_props += "</w:rPr>"
    para_props = f'<w:pPr><w:spacing w:after="{after}"/>'
    if align:
        para_props += f'<w:jc w:val="{align}"/>'
    para_props += "</w:pPr>"
    if not text:
        return f"<w:p>{para_props}</w:p>"
    return (
        f"<w:p>{para_props}<w:r>{run_props}"
        f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    )


def _body_paragraphs(body: str) -> list[str]:
    paragraphs = []
    for line in body.rstrip().split("\n"):
        paragraphs.append(_paragraph(line))
    return paragraphs


def build_docx(model: dict) -> bytes:
    """把文档模型渲染为 .docx 字节。"""

    pieces: list[str] = [
        _paragraph(model["title"], bold=True, size=H1_SIZE, color=BRAND, align="center"),
        _paragraph(""),
    ]
    for key, value in model["meta"]:
        pieces.append(_paragraph(f"{key}：{value}", size=META_SIZE))
    pieces.append(_paragraph(""))
    pieces.append(_paragraph(f"边界声明：{model.get('boundary', '')}", size=META_SIZE, after=240))

    for index, (title, body) in enumerate(model["sections"]):
        number = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]
        label = number[index] if index < len(number) else str(index + 1)
        pieces.append(_paragraph(f"{label}、{title}", bold=True, size=H2_SIZE, color=BRAND, after=160))
        pieces.extend(_body_paragraphs(body))
        pieces.append(_paragraph("", after=60))

    for line in model["footer"]:
        pieces.append(_paragraph(line, size=META_SIZE))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(pieces) + "</w:body></w:document>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()
