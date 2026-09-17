"""交付物导出：把一次任务的全部产出打包成可打印、可发送的报告。

支持两种出口：
- Markdown（开发者/归档）；
- Word .docx（普通用户，见 docx_export.py，纯标准库生成）。

两种出口共用同一个「文档模型」，保证内容一致（单一事实来源）。
服务重启后 TASKS 内存记录会丢，此时用检查点里的「前端视图」重建模型，
导出接口不会因为重启而 404。
"""

from __future__ import annotations

import re
from datetime import date

from .item_assessment import STATUS_LABEL
from .models import FeasibilityAssessment, Role, TaskRecord

ROLE_LABEL = {
    Role.CLAIMANT: "主张方（原告）视角",
    Role.DEFENDANT: "抗辩方（被告）视角",
    Role.JUDGE: "法官（中立）视角",
}

NUMERALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]

BOUNDARY = (
    "本报告由「案理」智能体自动生成，仅供学习与参考，不构成法律意见，不替代执业律师。"
    "金额由确定性规则逐项计算，可复现、可审计。"
)

FOOTER = [
    "落款：案理数字法律团队",
    f"出具日期：{date.today().isoformat()}",
    "数据说明：类案区间标注真实性与样本量（🟢 实证 / 🟡 演示 / 🔴 无样本）；引用核验不通过的法条与案号已自动剔除。",
]


def _body(body: str) -> str:
    """Markdown 里单个换行不渲染成换行，行尾补两个空格才是硬换行。"""

    return "\n".join(line + "  " for line in body.rstrip().split("\n"))


def _tier_label(tier: str) -> str:
    return {
        "real": "🟢 实证类案（人工核验）",
        "mixed": "🟡 混合样本（含演示数据）",
        "demo": "🟡 演示数据（虚构）",
        "none": "🔴 无类案样本",
    }.get(tier, "未标注")


def _tier_label_for(assessment, tier: str) -> str:
    """量级闸门拒答时，把数据等级说清楚：有样本但不可用 ≠ 一个样本都没有。"""

    if assessment is not None and getattr(assessment, "magnitude_mismatch", False):
        return "🔴 无可用类案样本（现有样本与本案损失量级不匹配）"
    return _tier_label(tier)


def export_model_from_record(record: TaskRecord, task_id: str) -> dict:
    """从完整任务记录构建文档模型。"""

    intake = record.extra.get("intake_source", "rules")
    llm_calls = record.extra.get("llm_calls", 0)
    assessment = record.assessment
    tier = getattr(assessment, "case_tier", "") if assessment else ""
    if not tier and assessment is not None:
        # 主干评估模型没有 case_tier 字段：按样本构成推导，避免导出显示「未标注」
        sample = int(getattr(assessment, "sample_size", 0) or 0)
        synthetic = int(getattr(assessment, "synthetic_sample_size", 0) or 0)
        if sample == 0:
            tier = "none"
        elif synthetic >= sample:
            tier = "demo"
        elif synthetic > 0:
            tier = "mixed"
        else:
            tier = "real"
    return {
        "title": "案理 · 法律分析报告",
        "meta": [
            ("报告编号", task_id),
            ("生成日期", date.today().isoformat()),
            ("分析视角", ROLE_LABEL.get(record.role, record.role.value)),
            (
                "案情理解方式",
                f"{'大模型抽取 + 规则校验' if intake == 'llm' else '规则引擎'}（模型参与 {llm_calls} 次）",
            ),
            ("类案数据等级", _tier_label_for(assessment, tier)),
        ],
        "sections": [(a.title, a.body) for a in record.artifacts],
        "footer": list(FOOTER),
        "boundary": BOUNDARY,
    }


_SECTION_SPLIT = re.compile(r"^=====\s*(.+?)\s*=====$", re.M)


def export_model_from_view(view: dict, task_id: str) -> dict:
    """从检查点保存的前端视图重建文档模型（服务重启后的导出路径）。"""

    role_value = str(view.get("role") or "claimant")
    role = next((item for item in Role if item.value == role_value), Role.CLAIMANT)
    assessment = view.get("assessment") or {}
    report = str(view.get("report") or "")
    sections: list[tuple[str, str]] = []
    parts = _SECTION_SPLIT.split(report)
    # split 结果：[前言, 标题1, 正文1, 标题2, 正文2, ...]
    for index in range(1, len(parts) - 1, 2):
        sections.append((parts[index].strip(), parts[index + 1].strip()))
    return {
        "title": "案理 · 法律分析报告",
        "meta": [
            ("报告编号", task_id),
            ("生成日期", date.today().isoformat()),
            ("分析视角", ROLE_LABEL.get(role, role_value)),
            ("来源", "服务重启后自检查点恢复"),
            ("类案数据等级", _tier_label(str(assessment.get("case_tier") or ""))),
        ],
        "sections": sections,
        "footer": list(FOOTER),
        "boundary": BOUNDARY,
    }


def render_markdown(model: dict) -> str:
    lines: list[str] = [f"# {model['title']}", "", "| 项目 | 内容 |", "| --- | --- |"]
    lines += [f"| {key} | {value} |" for key, value in model["meta"]]
    lines += ["", f"> **边界声明**：{BOUNDARY}", "", "---", ""]
    for index, (title, body) in enumerate(model["sections"]):
        number = NUMERALS[index] if index < len(NUMERALS) else str(index + 1)
        lines += [f"## {number}、{title}", "", _body(body), ""]
    lines += ["---", ""]
    for line in model["footer"]:
        if "：" in line:
            label, _, rest = line.partition("：")
            lines.append(f"**{label}**：{rest}  ")
        else:
            lines.append(f"{line}  ")
    return "\n".join(lines) + "\n"


def build_markdown_export(record: TaskRecord, task_id: str, report_text: str = "") -> str:
    """兼容入口：从任务记录导出 Markdown。"""

    return render_markdown(export_model_from_record(record, task_id))


def build_docx_export(record: TaskRecord | None, task_id: str) -> bytes:
    """兼容入口：从任务记录导出 Word（渲染统一走 docx_export.build_docx）。"""

    from .docx_export import build_docx

    return build_docx(export_model_from_record(record, task_id))


# ---------------------------------------------------------------------- 章节序号
# 交付物已经超过 12 份（含质证争点清单与模拟判决书），序号表要够长，
# 否则第 13 份之后会退化成「13、」这种阿拉伯数字，跟前面的「十二、」不统一。
CN_NUMERALS = [
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
]


def section_number(index: int) -> str:
    """第 index 份交付物的章节序号（0 起）。"""

    return CN_NUMERALS[index] if index < len(CN_NUMERALS) else str(index + 1)


# ---------------------------------------------------------------------- 金额认定表（CSV）
CSV_HEADER = [
    "序号", "赔偿项目", "主张金额(元)", "认定状态", "认定金额(元)",
    "认定依据", "法定标准", "缺失参数", "提示",
]


def _finding_rows(source) -> list[dict]:
    """把逐项认定结果统一成字典行：任务记录与检查点视图都可用。"""

    raw = (
        list(getattr(getattr(source, "profile", None), "item_findings", None) or [])
        if not isinstance(source, dict)
        else list(source.get("item_findings") or [])
    )
    rows: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            rows.append(item)
        else:
            rows.append(
                {
                    "item": item.item,
                    "claimed": item.claimed,
                    "status": item.status,
                    "counted": item.counted,
                    "basis": item.basis,
                    "standard": item.standard,
                    "missing_params": item.missing_params,
                    "note": item.note,
                }
            )
    return rows


def build_csv_export(source, task_id: str) -> str:
    """金额认定表：逐项列出「主张多少 / 认定成什么状态 / 认定多少钱 / 什么依据」。

    source 可以是任务记录，也可以是检查点里保存的前端视图（服务重启后的导出路径）。
    表格以「能被 Excel 直接打开并筛选」为准：表头与数据行在前，
    免责与口径说明放在末尾的说明行，不打断表头。
    调用方需按 UTF-8 BOM 写出，否则中文表头在 Excel 里会乱码。
    """

    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)

    for index, row in enumerate(_finding_rows(source), start=1):
        status = str(row.get("status", ""))
        writer.writerow(
            [
                index,
                row.get("item", ""),
                f"{float(row.get('claimed') or 0):.0f}",
                STATUS_LABEL.get(status, status),
                f"{float(row.get('counted') or 0):.0f}" if status == "supported" else "",
                row.get("basis", ""),
                row.get("standard", ""),
                "、".join(row.get("missing_params") or []),
                row.get("note", ""),
            ]
        )

    assessment = (source.get("assessment") if isinstance(source, dict) else None) or getattr(
        source, "assessment", None
    )
    if isinstance(assessment, dict):
        # 视图里是 asdict 的结果：重建成对象才能用到 sample_note 这类派生字段
        assessment = FeasibilityAssessment(**assessment)
    writer.writerow([])
    writer.writerow(["报告编号", task_id])
    writer.writerow(["生成日期", date.today().isoformat()])
    if assessment is not None:
        writer.writerow(["按现有证据可支持金额(约,元)", f"{assessment.legal_cap:.0f}"])
        if assessment.case_low is not None:
            writer.writerow(
                ["类案区间(约,元)", f"{assessment.case_low:.0f} - {assessment.case_high:.0f}"]
            )
        else:
            writer.writerow(["类案区间(约,元)", "暂不提供"])
        writer.writerow(["样本量(件)", assessment.sample_size])
        writer.writerow(["样本构成", assessment.sample_note])
        writer.writerow(
            [
                "结论状态",
                "证据不充分（待补证，本次不给结论性数字）"
                if assessment.provisional
                else "可给出结论性估算",
            ]
        )
    writer.writerow(
        [
            "说明",
            "金额由确定性规则逐项计算，不因证据不足而给主张金额打折；表中金额为按现有材料估算的约数。"
            "本表仅供学习与参考，不构成法律意见，使用前请人工复核。",
        ]
    )
    return buffer.getvalue()
