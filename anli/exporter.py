"""交付物导出：把一次任务的全部产出打包成一份可打印、可发送的 Markdown 报告。

排版目标：像一份律所出具的分析备忘录，而不是程序 dump——
有抬头、有元信息表、有目录化的章节、有落款与边界声明。
所有章节直接复用任务交付物，保证与网页展示内容一致（单一事实来源）。
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

from .item_assessment import STATUS_LABEL
from .models import Role, TaskRecord

ROLE_LABEL = {
    Role.CLAIMANT: "主张方（原告）视角",
    Role.DEFENDANT: "抗辩方（被告）视角",
    Role.JUDGE: "法官（中立）视角",
}

# 交付物已经超过 12 份（含质证争点清单与模拟判决书），序号表要够长，
# 否则第 13 份之后会退化成「13、」这种阿拉伯数字，跟前面的「十二、」不统一。
CN_NUMERALS = [
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
]


def section_number(index: int) -> str:
    """第 index 份交付物的章节序号（0 起）。"""

    return CN_NUMERALS[index] if index < len(CN_NUMERALS) else str(index + 1)


# Markdown 里单个换行不渲染成换行，行尾补两个空格才是硬换行
def _body(body: str) -> str:
    return "\n".join(line + "  " for line in body.rstrip().split("\n"))


def build_markdown_export(record: TaskRecord, task_id: str, report_text: str = "") -> str:
    """把任务交付物组装成 Markdown 报告文本。"""

    intake = record.extra.get("intake_source", "rules") if record else "rules"
    llm_calls = record.extra.get("llm_calls", 0) if record else 0
    assessment = record.assessment if record else None
    tier = (getattr(assessment, "case_tier", "") if assessment else "") or "unknown"
    tier_label = {
        "real": "🟢 实证类案（人工核验）",
        "mixed": "🟡 混合样本（含演示数据）",
        "demo": "🟡 演示数据（虚构）",
        "none": "🔴 无类案样本",
    }.get(tier, "未标注")

    lines: list[str] = [
        "# 案理 · 法律分析报告",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 报告编号 | {task_id} |",
        f"| 生成日期 | {date.today().isoformat()} |",
        f"| 分析视角 | {ROLE_LABEL.get(record.role, record.role.value) if record else '—'} |",
        f"| 案情理解方式 | {'大模型抽取 + 规则校验' if intake == 'llm' else '规则引擎'}（模型参与 {llm_calls} 次） |",
        f"| 类案数据等级 | {tier_label} |",
        "",
        "> **边界声明**：本报告由「案理」智能体自动生成，仅供学习与参考，不构成法律意见，不替代执业律师。重大金额、时效临近或涉人身安全事项，请立即咨询律师或申请法律援助。金额由确定性规则逐项计算，可复现、可审计。",
        "",
        "---",
        "",
    ]

    for index, artifact in enumerate(record.artifacts if record else []):
        number = section_number(index)
        lines.append(f"## {number}、{artifact.title}")
        lines.append("")
        lines.append(_body(artifact.body))
        lines.append("")

    lines += [
        "---",
        "",
        "**落款**：案理模拟法庭（数字法律团队）  ",
        f"**出具日期**：{date.today().isoformat()}  ",
        "**数据说明**：类案区间标注真实性与样本量（🟢 实证 / 🟡 演示 / 🔴 无样本）；引用核验不通过的法条与案号已自动剔除。",
    ]
    return "\n".join(lines) + "\n"


def _sections(record: TaskRecord | None) -> list[tuple[str, str]]:
    """交付物 → (标题, 正文)。

    三种导出格式都从这里取内容，保证与网页展示同源：
    页面上有的章节，导出里一定有；导出里也不会多出页面没有的章节。
    """

    return [(item.title, item.body) for item in (record.artifacts if record else [])]


# ---------------------------------------------------------------------- 金额认定表
CSV_HEADER = [
    "序号", "赔偿项目", "主张金额(元)", "认定状态", "认定金额(元)",
    "认定依据", "法定标准", "缺失参数", "提示",
]


def build_csv_export(record: TaskRecord | None, task_id: str) -> str:
    """金额认定表：逐项列出「主张多少 / 认定成什么状态 / 认定多少钱 / 什么依据」。

    表格以「能被 Excel 直接打开并筛选」为准：
    表头与数据行在前，免责与口径说明放在末尾的说明行，不打断表头。
    调用方需按 UTF-8 BOM 写出，否则中文表头在 Excel 里会乱码。
    """

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)

    findings = list(getattr(getattr(record, "profile", None), "item_findings", None) or [])
    for index, item in enumerate(findings, start=1):
        status = STATUS_LABEL.get(item.status, item.status)
        writer.writerow(
            [
                index,
                item.item,
                f"{item.claimed:.0f}",
                status,
                f"{item.counted:.0f}" if item.status == "supported" else "",
                item.basis,
                item.standard,
                "、".join(item.missing_params),
                item.note,
            ]
        )

    assessment = getattr(record, "assessment", None) if record else None
    writer.writerow([])
    writer.writerow(["报告编号", task_id])
    writer.writerow(["生成日期", date.today().isoformat()])
    if assessment is not None:
        writer.writerow(["按现有证据可支持金额(约,元)", f"{assessment.legal_cap:.0f}"])
        writer.writerow(["类案区间(约,元)", f"{assessment.case_low:.0f} - {assessment.case_high:.0f}"])
        writer.writerow(["样本量(件)", assessment.sample_size])
        writer.writerow(["样本构成", assessment.sample_note])
        writer.writerow(
            ["结论状态", "证据不充分（待补证，本次不给结论性数字）" if assessment.provisional else "可给出结论性估算"]
        )
    writer.writerow(
        [
            "说明",
            "金额由确定性规则逐项计算，不因证据不足而给主张金额打折；表中金额为按现有材料估算的约数。"
            "本表仅供学习与参考，不构成法律意见，使用前请人工复核。",
        ]
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------- Word 导出
# 不引第三方库，用 zipfile 手写最小 OOXML 包：
# [Content_Types].xml + _rels/.rels + word/document.xml 三件即可被 Word 打开。
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _xml_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _docx_paragraph(text: str, bold: bool = False, size: int = 21) -> str:
    """一个段落。用直接格式而不是样式表，省掉 styles.xml 这一层依赖。"""

    props = ("<w:b/>" if bold else "") + f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    run = (
        f"<w:r><w:rPr>{props}</w:rPr>"
        f'<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r>'
    )
    return f'<w:p><w:pPr><w:spacing w:after="80"/></w:pPr>{run}</w:p>'


def build_docx_export(record: TaskRecord | None, task_id: str) -> bytes:
    """把交付物导出为 .docx（Word 可直接打开的 Word 版分析报告）。

    章节与 Markdown 版同源（都走 `_sections`），因此不存在「导出的 Word 少了章节」。
    """

    role_label = ROLE_LABEL.get(record.role, record.role.value) if record else "—"

    paragraphs = [
        _docx_paragraph("案理 · 法律分析报告", bold=True, size=36),
        _docx_paragraph(f"报告编号：{task_id}"),
        _docx_paragraph(f"生成日期：{date.today().isoformat()}"),
        _docx_paragraph(f"分析视角：{role_label}"),
        _docx_paragraph(
            "边界声明：本报告由「案理」智能体自动生成，仅供学习与参考，不构成法律意见，不替代执业律师。"
            "金额由确定性规则逐项计算，可复现、可审计。"
        ),
    ]

    for index, (title, body) in enumerate(_sections(record)):
        paragraphs.append(_docx_paragraph(f"{section_number(index)}、{title}", bold=True, size=28))
        for line in body.rstrip().split("\n"):
            paragraphs.append(_docx_paragraph(line))

    paragraphs.append(_docx_paragraph("落款：案理模拟法庭（数字法律团队）"))
    paragraphs.append(_docx_paragraph(f"出具日期：{date.today().isoformat()}"))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>'
        + "".join(paragraphs)
        # pgMar 的 header / footer / gutter 在 OOXML 里是必需属性，缺了 XSD 校验会报错
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1418" w:bottom="1440" w:left="1418" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        package.writestr("_rels/.rels", _DOCX_RELS)
        package.writestr("word/document.xml", document)
    return buffer.getvalue()
