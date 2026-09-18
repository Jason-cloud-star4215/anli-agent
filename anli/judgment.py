"""裁判推演（模拟）：法官视角基于逐项认定与类案参照出具的推演草稿。

三条纪律：
1. 文书首尾必须显著标注「智能体模拟、不发生法律效力」；
2. 裁判金额只能取自可行性评估的确定性计算结果，不得另编；
3. 「类案参照」一节只引用 🟢 实证类案；库为空时如实声明，
   预留标准段落格式，补录后自动引用。

为什么只给法官视角：判决书是人民法院的专属文书；主张方/抗辩方在 documents.py 里
拿到的是当事人自己的文书（起诉状／答辩状），不会看到「本院认为」「判决结果」这类裁判口径。
"""

from __future__ import annotations

from datetime import date

from .models import CaseProfile, Citation, FeasibilityAssessment


def render_judgment(
    profile: CaseProfile,
    assessment: FeasibilityAssessment | None,
    real_citations: list[Citation],
) -> str:
    """生成模拟裁判推演草稿正文（仅法官视角）。"""

    head = [
        "（本推演由「案理」智能体模拟生成，仅供学习与参考，不构成法律意见，",
        "不发生法律效力，不替代人民法院裁判。使用前请交由执业律师复核。）",
        "",
        "原告（主张方）：本案当事人",
        "被告（抗辩方）：本案当事人",
        f"案由：{profile.cause}",
        "",
        "一、诉讼请求",
    ]
    if assessment is None:
        return "\n".join(head + ["评估尚未生成，无法出具推演草稿。"])

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
        "三、推演意见",
    ]

    supported = [
        (item, info)
        for item, info in (profile.blocked_items or {}).items()
        if info.get("kind") == "supported"
    ]
    counted_lines = [f"{item} {float(info.get('amount') or 0):,.0f} 元" for item, info in supported]
    points: list[str] = []
    if profile.claim_items:
        points.append(
            "关于赔偿项目：经逐项审查，以下项目有证据支撑、予以认定："
            + "、".join(
                f"{item} {amount:,.0f} 元" for item, amount in profile.claim_items.items()
            )
            + "。"
            + ("另予认定：" + "、".join(counted_lines) + "。" if counted_lines else "")
        )
    else:
        points.append("关于赔偿项目：原告所列各项均未能提供有效凭证，属举证不能，本案均不予认定。")
    excluded = list((profile.blocked_items or {}).keys())
    if excluded:
        points.append(
            "关于未能计入的项目："
            + "、".join(excluded)
            + " 依证据状况与法定标准另行确定，本案暂不计入；原告可补充证据后另行主张。"
        )
    # 责任不在被告：这一档必须说透，否则推演会被读成「只是证据不够」
    no_liability = bool((profile.extra or {}).get("claim_has_no_basis")) or (
        profile.responsibility_ratio <= 0
    )
    if no_liability:
        points.append(
            "关于责任：认定书显示被告不承担责任，原告的赔偿请求缺乏责任基础，"
            "本案不产生赔偿责任；原告如对认定有异议，应走复核程序而不是在本案中主张。"
        )
        points.append("关于类案参照：责任基础不成立，金额层面的类案参照不再具有决定意义。")
    else:
        points.append(
            f"关于责任比例：按被告承担 {profile.responsibility_ratio:.0%} 计算，"
            f"被告应赔偿金额为 {assessment.legal_cap:,.0f} 元。"
        )
        points.append("关于类案参照：详见下节。")
    lines += [f"{index}. {text}" for index, text in enumerate(points, start=1)]
    lines += [
        "",
        "四、类案参照",
    ]
    if real_citations:
        for citation in real_citations:
            lines.append(f"- {citation.label}：{citation.note}")
    else:
        if no_liability:
            lines.append("本案审理期间未检索到可供参考的实证类案（🟢）。")
        else:
            lines.append(
                "本案审理期间未检索到可供参考的实证类案（🟢）。"
                "本推演仅依据法定标准与在案证据作出；实证类案库补录后，本节将自动引用真实判决。"
            )
    lines += ["", "五、推演结论"]
    if no_liability:
        lines += [
            "一、被告不承担赔偿责任，原告的诉讼请求缺乏责任基础；",
            "二、原告如认为事故认定有误，应另行申请复核，本案不能通过举证改变责任认定。",
            "",
            "（再次提示：本推演为智能体模拟文书，不发生法律效力。）",
        ]
    else:
        lines += [
            f"一、按现有证据与法定标准，可支持金额约为 {assessment.legal_cap:,.0f} 元；",
            "二、其余诉讼请求因证据或法定标准不足，按现有材料难以支持。",
            "",
            "（再次提示：本推演为智能体模拟文书，不发生法律效力；未支持部分多为证据问题，"
            "补强证据后预期将发生变化。）",
        ]
    lines += [
        "",
        "出具方：案理智能体（模拟推演，非审判组织、非裁判文书）",
        f"日期：{date.today().isoformat()}",
    ]
    return "\n".join(lines)
