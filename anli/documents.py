"""文书草稿：按身份出具当事人自己的文书，不给用户"法院会怎么判"的错觉。

为什么不是判决书：判决书是人民法院的专属文书，智能体出具判决书形式的文本，
即使加了免责声明，复制出去也容易被当成裁判结果；而当事人真正要写、也真正用得上的是
起诉状（主张方）、答辩状（抗辩方）。法官视角需要的是中立推演，见 judgment.py。

三条纪律：
1. 金额只取自可行性评估的确定性结果，不另编；
2. 不编造法院名称、案号、当事人姓名——需要用户填写的一律留占位符；
3. 文书首尾必须标注「草稿 + 需人工复核」，不得出现胜诉承诺。
"""

from __future__ import annotations

from datetime import date

from .cross_exam import build_challenges
from .models import CaseProfile, Citation, FeasibilityAssessment, Role

DRAFT_HEAD = (
    "（本文书由「案理」智能体根据你提供的材料自动生成，属草稿，"
    "需经执业律师或你本人核对后使用；不构成法律意见，不替代律师。）"
)

ROLE_NOTE = {
    Role.CLAIMANT: "（提示：本文书为参考草稿，是否起诉、何时起诉请结合时效与成本判断。）",
    Role.DEFENDANT: "（提示：本文书为参考草稿，具体答辩策略请结合对方证据与开庭情况调整。）",
}


def _amount(value: float | None) -> str:
    return "待定" if value is None else f"{value:,.0f} 元"


def _support_sentence(assessment: FeasibilityAssessment | None, blocked: list[str]) -> str:
    """「可支持金额」这句话在评估缺失时必须换个说法，不能写成「约为 待定」。"""

    tail = ("未提供凭证的项目（" + "、".join(blocked) + "）待补充证据后另行主张。") if blocked else (
        "暂未发现完全无凭证的项目，仍以原件质证为准。"
    )
    if assessment is None:
        return "2. 可支持金额需在补齐材料后评估；" + tail
    return f"2. 按现有材料，可支持金额约为 {_amount(assessment.legal_cap)}；" + tail


def _claim_lines(profile: CaseProfile) -> list[str]:
    """诉讼请求的明细：主张过的项目要列全，不能只列已计入的——
    「合计 12,000 元（明细：仅医疗费）」会让读者以为其他项目没主张过。"""

    lines: list[str] = []
    for item, amount in profile.claim_items.items():
        if isinstance(amount, (int, float)):
            lines.append(f"{item} {amount:,.0f} 元")
        else:
            lines.append(f"{item}（金额待补充）")
    for item, info in (profile.blocked_items or {}).items():
        if item in profile.claim_items:
            continue
        amount = info.get("amount")
        if isinstance(amount, (int, float)) and amount:
            lines.append(f"{item} {amount:,.0f} 元（待补证后另行主张）")
        else:
            lines.append(f"{item}（金额待补充）")
    return lines


def _evidence_lines(profile: CaseProfile, role: Role) -> list[str]:
    if role == Role.DEFENDANT:
        gaps = [f"{item}（{info.get('basis') or '缺证据'}）" for item, info in (profile.blocked_items or {}).items()]
        return gaps or ["对方主张的各项已提供相应材料（以质证为准）"]
    lines = [f"{name}（{profile.evidence_sources.get(name, '来源待确认')}）" for name in profile.available_evidence]
    return lines or ["尚未提交书面材料，需补充后再主张"]


def _sentence(text: str) -> str:
    """把一句说理补成完整的句子，避免多个要点连排时读起来断掉。"""

    text = (text or "").strip()
    if not text:
        return text
    return text if text[-1] in "。；！？" else text + "。"


def _no_basis_notice(profile: CaseProfile, role: Role) -> list[str]:
    """责任认定显示被告不担责时，文书开头必须先把这个说清楚。

    主张方最容易被误导：他会以为「材料补齐就能拿到钱」，实际上金额不是障碍、责任才是。
    """

    if not (profile.extra or {}).get("claim_has_no_basis"):
        return []
    basis = (profile.extra or {}).get("responsibility_basis") or "责任认定显示你方不承担责任"
    if role == Role.DEFENDANT:
        return [
            "",
            f"（重要提示：{basis}。按描述你方不承担赔偿责任，"
            "下面这份答辩状的重点是固定「责任不在你方」这一抗辩，而不是逐项压价。）",
        ]
    return [
        "",
        f"（重要提示：{basis}。本文书所载赔偿请求**缺乏责任基础**，"
        "即使证据齐全通常也不会得到支持；除非事故认定本身被复核纠正，"
        "否则不建议据此起诉。请先按「策略建议」核对认定书。）",
    ]


def render_document(
    profile: CaseProfile,
    assessment: FeasibilityAssessment | None,
    role: Role,
    real_citations: list[Citation],
    judgment_renderer=None,
) -> tuple[str, str]:
    """按身份返回 (标题, 正文)。法官视角交给模拟推演渲染器。"""

    if role == Role.JUDGE:
        renderer = judgment_renderer
        if renderer is None:  # pragma: no cover - 正常路径一定会传入
            from .judgment import render_judgment

            renderer = render_judgment
        return "裁判推演（模拟，仅供学习参考）", renderer(profile, assessment, real_citations)
    if role == Role.DEFENDANT:
        return "民事答辩状（草稿）", render_defense(profile, assessment)
    return "民事起诉状（草稿）", render_complaint(profile, assessment)


def render_complaint(profile: CaseProfile, assessment: FeasibilityAssessment | None) -> str:
    """民事起诉状草稿（主张方）。法院名称留占位符，不编造管辖。"""

    claim_total = assessment.expectation if assessment else sum(profile.claim_items.values())
    detail = "、".join(_claim_lines(profile)) or "（各项损失金额待补充）"
    blocked = list((profile.blocked_items or {}).keys())
    lines = [
        DRAFT_HEAD,
        *_no_basis_notice(profile, Role.CLAIMANT),
        "",
        "民事起诉状（草稿）",
        "",
        "原告：本案当事人（请补全姓名／名称、性别、出生日期、住址、联系方式）",
        "被告：本案当事人（请补全名称、住所地、联系方式）",
        f"案由：{profile.cause}",
        "",
        "一、诉讼请求",
        f"1. 请求判令被告赔偿原告各项损失合计 {claim_total:,.0f} 元（明细：{detail}）。",
        _support_sentence(assessment, blocked),
        "3. 本案诉讼费用由被告承担。",
        "",
        "二、事实与理由",
        profile.description or profile.summary,
        f"责任认定：按现有材料，被告应承担 {profile.responsibility_ratio:.0%} 的责任。",
        "赔偿项目与依据：",
    ]
    findings = profile.item_findings or []
    if findings:
        for index, item in enumerate(findings, start=1):
            basis = getattr(item, "basis", "") or "需结合证据认定"
            lines.append(f"{index}. {item.item}：主张 {item.claimed:,.0f} 元；{basis}")
    else:
        lines.append("1. （尚未识别到具体赔偿项目，请在补充材料后完善）")
    lines += [
        "",
        "三、证据清单",
    ]
    lines += [f"{index}. {text}" for index, text in enumerate(_evidence_lines(profile, Role.CLAIMANT), start=1)]
    lines += [
        "",
        "四、此致",
        "____人民法院（请按被告住所地或侵权行为地确定管辖后填写；"
        f"{profile.region or '适用地区'}为本次分析采用的裁判尺度参照地）",
        "",
        "具状人：本案当事人（签名／盖章）",
        f"日期：{date.today().isoformat()}",
        "",
        ROLE_NOTE[Role.CLAIMANT],
    ]
    return "\n".join(lines)


def render_defense(profile: CaseProfile, assessment: FeasibilityAssessment | None) -> str:
    """民事答辩状草稿（抗辩方）。质证意见复用结构化质证清单，金额不另编。"""

    challenges = build_challenges(profile)
    if assessment is None:
        amount_sentence = "按现有证据与责任比例，对方主张的金额尚需结合其举证逐项核定，超出部分请求不予支持。"
    else:
        amount_sentence = (
            f"按现有证据与责任比例，可能被支持的金额约为 {_amount(assessment.legal_cap)}，"
            "超出部分请求不予支持。"
        )
    lines = [
        DRAFT_HEAD,
        *_no_basis_notice(profile, Role.DEFENDANT),
        "",
        "民事答辩状（草稿）",
        "",
        "答辩人：本案当事人（请补全名称、住所地、联系方式）",
        "被答辩人：对方当事人",
        f"案由：{profile.cause}",
        "",
        "一、答辩意见",
    ]
    opinion_count = 0
    if challenges:
        for index, challenge in enumerate(challenges, start=1):
            lines.append(
                f"{index}. 关于{challenge.target_item}（涉及约 {challenge.impact:,.0f} 元）："
                f"{_sentence(challenge.argument)}"
            )
            opinion_count = index
    else:
        lines.append("1. 对方主张的各项暂未发现明显举证漏洞，建议结合原件质证后确定答辩要点。")
        opinion_count = 1
    lines += [
        f"{opinion_count + 1}. {amount_sentence}",
        "",
        "二、事实与理由",
        profile.description or profile.summary,
        f"责任比例意见：按现有材料，答辩人应承担 {profile.responsibility_ratio:.0%} 的责任；"
        "对认定书有异议的，应在复核期内申请复核。",
        "",
        "三、对方举证情况（供质证使用）",
    ]
    lines += [f"{index}. {text}" for index, text in enumerate(_evidence_lines(profile, Role.DEFENDANT), start=1)]
    lines += [
        "",
        "四、答辩请求",
        "请求驳回被答辩人缺乏证据支持的诉讼请求；对超出法定标准与证据范围的部分不予支持。",
        "",
        "答辩人：本案当事人（签名／盖章）",
        f"日期：{date.today().isoformat()}",
        "",
        ROLE_NOTE[Role.DEFENDANT],
    ]
    return "\n".join(lines)
