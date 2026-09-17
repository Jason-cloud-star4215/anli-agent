"""模拟判决书：法官视角基于逐项认定与类案参照出具的草稿文书。

三条纪律：
1. 文书首尾必须显著标注「智能体模拟、不发生法律效力」；
2. 裁判金额只能取自可行性评估的确定性计算结果，不得另编；
3. 「本院参考类案」一节只引用 🟢 实证类案；库为空时如实声明，
   预留标准段落格式，补录后自动引用。
"""

from __future__ import annotations

from datetime import date

from .models import CaseProfile, Citation, FeasibilityAssessment


def render_judgment(
    profile: CaseProfile,
    assessment: FeasibilityAssessment | None,
    real_citations: list[Citation],
) -> str:
    """生成模拟判决书草稿正文。"""

    head = [
        "（本判决由「案理」智能体模拟生成，仅供学习与参考，不构成法律意见，",
        "不发生法律效力，不替代人民法院裁判。使用前请交由执业律师复核。）",
        "",
        "原告（主张方）：本案当事人",
        "被告（抗辩方）：本案当事人",
        f"案由：{profile.cause}",
        "",
        "一、诉讼请求",
    ]
    if assessment is None:
        return "\n".join(head + ["评估尚未生成，无法出具判决草稿。"])

    claim_detail = "、".join(
        f"{item} {amount:,.0f} 元" for item, amount in profile.claim_items.items()
    )
    lines = head + [
        f"请求判令被告赔偿合计 {assessment.expectation:,.0f} 元"
        + (f"（明细：{claim_detail}）" if claim_detail else "。"),
        "",
        "二、查明的基本事实",
        profile.description or profile.summary,
        f"责任认定：被告承担 {profile.responsibility_ratio:.0%} 的责任。",
        "",
        "三、本院认为",
    ]

    supported = [
        (item, info)
        for item, info in (profile.blocked_items or {}).items()
        if info.get("kind") == "supported"
    ]
    counted_lines = [f"{item} {float(info.get('amount') or 0):,.0f} 元" for item, info in supported]
    if profile.claim_items:
        lines.append(
            "1. 关于赔偿项目：经逐项审查，以下项目有证据支撑、予以认定："
            + "、".join(
                f"{item} {amount:,.0f} 元" for item, amount in profile.claim_items.items()
            )
            + "。"
        )
    else:
        lines.append(
            "1. 关于赔偿项目：原告所列各项均未能提供有效凭证，属举证不能，本案均不予认定。"
        )
    if supported:
        lines.append("   另予认定：" + "、".join(counted_lines) + "。")
    excluded = list((profile.blocked_items or {}).keys())
    if excluded:
        lines.append(
            "2. 关于未能计入的项目："
            + "、".join(excluded)
            + " 依证据状况与法定标准另行确定，本案暂不计入；原告可补充证据后另行主张。"
        )
    lines += [
        f"3. 关于责任比例：按被告承担 {profile.responsibility_ratio:.0%} 计算，"
        f"被告应赔偿金额为 {assessment.legal_cap:,.0f} 元。",
        "4. 关于类案参照：详见下节。",
        "",
        "四、本院参考类案",
    ]
    if real_citations:
        for citation in real_citations:
            lines.append(f"- {citation.label}：{citation.note}")
    else:
        lines.append(
            "本案审理期间未检索到可供参考的实证类案（🟢）。"
            "本判决仅依据法定标准与在案证据作出；实证类案库补录后，本节将自动引用真实判决。"
        )
    lines += [
        "",
        "五、判决结果",
        f"一、被告于本判决生效之日起十日内赔偿原告合计 {assessment.legal_cap:,.0f} 元；",
        "二、驳回原告的其他诉讼请求。",
        "",
        "（再次提示：本判决为智能体模拟文书，不发生法律效力；未支持部分多为证据问题，"
        "补强证据后预期将发生变化。）",
        "",
        f"模拟审判组织：案理数字法律团队",
        f"裁判日期：{date.today().isoformat()}",
    ]
    return "\n".join(lines)
