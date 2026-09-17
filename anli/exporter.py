"""交付物导出：把一次任务的全部产出打包成一份可打印、可发送的 Markdown 报告。

排版目标：像一份律所出具的分析备忘录，而不是程序 dump——
有抬头、有元信息表、有目录化的章节、有落款与边界声明。
所有章节直接复用任务交付物，保证与网页展示内容一致（单一事实来源）。
"""

from __future__ import annotations

from datetime import date

from .models import Role, TaskRecord

ROLE_LABEL = {
    Role.CLAIMANT: "主张方（原告）视角",
    Role.DEFENDANT: "抗辩方（被告）视角",
    Role.JUDGE: "法官（中立）视角",
}

# Markdown 里单个换行不渲染成换行，行尾补两个空格才是硬换行
def _body(body: str) -> str:
    return "\n".join(line + "  " for line in body.rstrip().split("\n"))


def build_markdown_export(record: TaskRecord, task_id: str, report_text: str = "") -> str:
    """把任务交付物组装成 Markdown 报告文本。"""

    numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]
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
        number = numerals[index] if index < len(numerals) else str(index + 1)
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
