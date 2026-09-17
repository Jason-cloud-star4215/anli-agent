"""结构化质证：把「对方会怎么打」变成可核对的争点清单。

质证不是自由辩论，每一次挑战必须落到五个字段上：

- target_item：针对哪一项赔偿请求；
- target_evidence：针对哪几份证据；
- objection：明确的异议类型（举证不能 / 计算依据异议）；
- argument：说理文字；
- impact：涉及金额，由确定性规则从逐项认定结果取值。

金额与认定状态一律不由大模型生成。后续接入模型时，
模型只允许改写 argument 的说理文字，越界内容直接丢弃。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .item_assessment import ItemFinding
from .models import CaseProfile, Role

OBJECTION_LABEL = {
    "no_evidence": "举证不能",
    "standard_required": "计算依据异议",
}


@dataclass
class Challenge:
    """一条结构化质证挑战。"""

    target_item: str        # 针对的赔偿项目
    target_evidence: str    # 针对的证据
    objection: str          # no_evidence | standard_required
    argument: str           # 说理文字
    impact: float           # 涉及金额（元）
    basis: str              # 依据（来自逐项认定的认定理由）
    if_upheld: str          # 挑战成立的后果

    def to_dict(self) -> dict:
        return asdict(self)


def build_challenges(profile: CaseProfile) -> list[Challenge]:
    """从逐项认定结果生成抗辩方的质证清单，按涉及金额从大到小排序。

    规则与 item_assessment 的认定逻辑严格对应：
    - unsupported：证据完全缺失 → 举证不能，挑战成立则该项 0 元计入；
    - standard：事实有据但缺参数 → 计算依据异议，主张金额不直接采信。
    已获全额支持（supported）的项目不生成挑战——有凭证的项目不存在"抗掉"空间，
    生成假挑战反而会误导抗辩方。
    """

    challenges: list[Challenge] = []
    for item in profile.item_findings or []:
        if not isinstance(item, ItemFinding) or item.status == "supported":
            continue
        evidence = "、".join(item.required) if item.required else "相关证据"
        if item.status == "unsupported":
            challenges.append(
                Challenge(
                    target_item=item.item,
                    target_evidence=evidence,
                    objection="no_evidence",
                    argument=(
                        f"对方未提供{evidence}，{item.item} {item.claimed:.0f} 元"
                        "属于举证不能，不应计入赔偿"
                    ),
                    impact=float(item.claimed),
                    basis=item.basis,
                    if_upheld="该项不予认定，按 0 元计入",
                )
            )
        elif item.status == "standard":
            standard = item.standard or "法定标准"
            challenges.append(
                Challenge(
                    target_item=item.item,
                    target_evidence=evidence,
                    objection="standard_required",
                    argument=(
                        f"{item.item}即便事实成立，也须{standard}；"
                        f"对方主张 {item.claimed:.0f} 元没有计算依据"
                    ),
                    impact=float(item.claimed),
                    basis=item.basis,
                    if_upheld="按法定标准与参数另行计算，主张金额不直接采信",
                )
            )
    challenges.sort(key=lambda challenge: challenge.impact, reverse=True)
    return challenges


def total_at_stake(challenges: list[Challenge]) -> float:
    """全部挑战涉及金额之和，即对方理论上的抗辩空间。"""

    return round(sum(challenge.impact for challenge in challenges), 2)


_HEAD = {
    Role.DEFENDANT: (
        "以下是你对对方各项主张的质证要点，逐项照此抗辩；"
        "对方补不出对应材料的部分，坚持不予认可："
    ),
    Role.JUDGE: (
        "本案质证争点如下，请双方围绕争点补充举证；"
        "举证责任与认定后果由系统按认定逻辑标注："
    ),
    Role.CLAIMANT: (
        "对方大概率会按下面的清单攻击你的主张，请对照提前补强材料："
    ),
}

_TAIL = {
    Role.DEFENDANT: (
        "提示：以上金额是对方主张里的风险敞口，不等于你能少赔的确定数额；"
        "对方补齐材料后系统会重新核算。"
    ),
    Role.JUDGE: (
        "提示：争点金额为双方主张差额的上界，最终认定以双方补交的证据为准。"
    ),
    Role.CLAIMANT: (
        "提示：涉及金额是对方可能攻击的部分，不等于会全部被砍掉；"
        "按上面的材料清单提前补强，可守住大部分主张。"
    ),
}


def render_challenges(challenges: list[Challenge], role: Role = Role.CLAIMANT) -> str:
    """把质证清单渲染进交付物；同一份清单，三种身份看到三种措辞。"""

    if not challenges:
        return (
            "逐项认定中所有项目都有材料支撑，对方没有明显的攻击空间；"
            "仍建议保留全部凭证原件备查。"
        )
    lines: list[str] = [_HEAD.get(role, _HEAD[Role.CLAIMANT]), ""]
    for index, challenge in enumerate(challenges, start=1):
        lines.append(
            f"{index}. 【{OBJECTION_LABEL.get(challenge.objection, challenge.objection)}】"
            f"{challenge.target_item}｜涉及金额 {challenge.impact:.0f} 元"
        )
        lines.append(f"   针对证据：{challenge.target_evidence}")
        lines.append(f"   抗辩理由：{challenge.argument}")
        lines.append(f"   依据：{challenge.basis}")
        lines.append(f"   若挑战成立：{challenge.if_upheld}")
        lines.append("")
    lines.append(
        f"合计涉及金额 {total_at_stake(challenges):.0f} 元（对方主张的可争辩部分，非确定减免额）。"
    )
    lines.append(_TAIL.get(role, _TAIL[Role.CLAIMANT]))
    return "\n".join(lines)
