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

from .models import Role, TaskRecord

ROLE_LABEL = {
    Role.CLAIMANT: "主张方（原告）视角",
    Role.DEFENDANT: "抗辩方（被告）视角",
    Role.JUDGE: "法官（中立）视角",
}

NUMERALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]

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


def export_model_from_record(record: TaskRecord, task_id: str) -> dict:
    """从完整任务记录构建文档模型。"""

    intake = record.extra.get("intake_source", "rules")
    llm_calls = record.extra.get("llm_calls", 0)
    assessment = record.assessment
    tier = getattr(assessment, "case_tier", "") if assessment else ""
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
            ("类案数据等级", _tier_label(tier)),
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
