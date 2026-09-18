"""规划决策：让模型参与「怎么查、要不要再来一轮、先走哪条路」。

之前这三件事全是编排层写死的：检索词是「案由 + 争议焦点前 3 项」拼出来的，
是否追加轮次由规则数数决定，行动路径是固定四行文本——所以答辩时很难说清
「智能体到底自主规划了什么」。这里把它们改成**模型参与 + 规则兜底**。

三条原则：

1. **模型只做选择与排序，不产出新事实**。检索词必须落在案由、争议焦点、
   赔偿项目与证据词表之内；行动路径只能在既有模板里排序，不得新增路径；
   金额永远由规则层计算。
2. **每次都能回退**。模型不可用、超时、返回非法结构或越界内容，
   一律退回规则版结果，主流程不中断。
3. **每次都有留痕**。每个决策产出一个 `PlanningDecision`，
   通过 `plan_decided` 事件推给前端，并写进交付物，避免「模型干了什么」说不清。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .intake import EVIDENCE_VOCAB, ITEM_ALIASES
from .models import CaseProfile, CriticFinding, Severity

# 行动路径模板：模型只能排序，不能新增、改名或改写措辞
ACTION_TEMPLATES: list[dict[str, str]] = [
    {"key": "negotiate", "text": "协商：成本最低，需准备证据清单与书面主张。"},
    {"key": "complaint", "text": "投诉/调解：向主管部门或调解组织申请，周期较短。"},
    {"key": "litigation", "text": "仲裁/诉讼：成本与周期上升，需确认时效与管辖。"},
]
ACTION_LABELS = {"negotiate": "协商", "complaint": "投诉/调解", "litigation": "仲裁/诉讼"}
ACTION_DISCLAIMER = "提示：本方案为参考性质，不构成法律意见，重大金额或时效临近请咨询执业律师。"

ACTION_KEYS = [item["key"] for item in ACTION_TEMPLATES]
ACTION_TEXT = {item["key"]: item["text"] for item in ACTION_TEMPLATES}


@dataclass
class PlanningDecision:
    """一次规划决策。跨模块契约，字段稳定。"""

    kind: str                 # research_keywords / continue_round / action_order
    source: str               # llm 或 rules；回退时必须是 rules
    detail: str               # 供前端展示与报告留痕的一句话
    payload: dict = field(default_factory=dict)

    @property
    def from_llm(self) -> bool:
        return self.source == "llm"


# ---------------------------------------------------------------------- 检索词
def rule_keywords(profile: CaseProfile) -> list[str]:
    """规则版检索词：案由 + 争议焦点。模型不可用时用它。"""

    return [profile.cause] + list(profile.disputed_points[:3])


def allowed_keywords(profile: CaseProfile) -> set[str]:
    """模型可以选用的检索词白名单。

    只允许四类：案由、争议焦点、赔偿项目（含别名）、证据名称（含别名）。
    任何一个词都能在法条库或证据词表里找到出处，避免模型自己造概念。
    """

    allowed: set[str] = {profile.cause, *profile.disputed_points}
    allowed |= set(EVIDENCE_VOCAB)
    allowed |= set(ITEM_ALIASES.get(profile.cause, {}))
    for values in EVIDENCE_VOCAB.values():
        allowed |= set(values)
    for aliases in ITEM_ALIASES.get(profile.cause, {}).values():
        allowed |= set(aliases)
    return {item for item in allowed if item}


RESEARCH_SYSTEM = """你是法律检索规划助手，负责决定这次要检索哪些关键词。
只输出 JSON，不要任何解释：
{"keywords": ["关键词1", "关键词2"], "reason": "一句话说明为什么先查这些"}

硬约束：
1. 关键词只能从用户给出的候选词里选，不得自造新词；
2. 最多 6 个，按检索优先级排序；
3. 至少包含案由本身。"""


def plan_research(profile: CaseProfile, llm=None) -> PlanningDecision:
    """规划检索关键词。模型不可用或越界时退回规则版。"""

    fallback = rule_keywords(profile)
    rules_detail = "规则版检索词：" + "、".join(fallback)
    if llm is None or not getattr(llm, "available", False):
        return PlanningDecision(
            "research_keywords", "rules", rules_detail, {"keywords": fallback, "dropped": []}
        )

    allowed = allowed_keywords(profile)
    candidates = sorted(allowed)
    prompt = (
        f"案由：{profile.cause}\n"
        f"争议焦点：{'、'.join(profile.disputed_points) or '未识别'}\n"
        f"用户已提交材料：{'、'.join(profile.available_evidence) or '无'}\n"
        f"缺失材料：{'、'.join(profile.missing_evidence) or '无'}\n"
        f"候选词（只能从中选）：{'、'.join(candidates[:60])}"
    )
    try:
        payload = llm.chat_json(
            RESEARCH_SYSTEM, prompt, max_tokens=300, purpose="检索词规划"
        )
    except Exception:  # noqa: BLE001 - 模型失败必须退回规则版
        return PlanningDecision(
            "research_keywords",
            "rules",
            rules_detail + "（模型不可用，已回退）",
            {"keywords": fallback, "dropped": []},
        )

    raw = [str(item).strip() for item in (payload.get("keywords") or [])]
    kept = [item for item in raw if item in allowed]
    dropped = [item for item in raw if item and item not in allowed]
    # 去重后接上规则词兜底，保证案由一定在里面
    keywords = list(dict.fromkeys(kept + fallback))[:6]
    detail = "模型规划检索词：" + ("、".join(kept) if kept else "（模型未给出可用词，已用规则词）")
    if dropped:
        detail += f"；越界丢弃 {len(dropped)} 个"
    return PlanningDecision(
        "research_keywords",
        "llm",
        detail,
        {"keywords": keywords, "dropped": dropped},
    )


# ---------------------------------------------------------------------- 轮次判定
def open_findings(findings: list[CriticFinding]) -> list[CriticFinding]:
    """未解决、且严重度为中或高的复核意见。"""

    return [
        item
        for item in findings
        if not item.resolved and item.severity in {Severity.HIGH, Severity.MEDIUM}
    ]


ROUND_SYSTEM = """你是复核轮次决策助手，判断是否还需要再走一轮「修订 → 对方反驳 → 再复核」。
只输出 JSON：{"continue": true 或 false, "reason": "一句话理由"}
判断依据：未解决的问题是否会影响结论数字或法律依据。已经解决、或只涉及措辞的问题不必再来一轮。"""


def should_continue(
    findings: list[CriticFinding], round_no: int, max_rounds: int, llm=None
) -> PlanningDecision:
    """是否追加一轮复核修订。

    两条硬边界不交给模型：没有问题就不继续；到了轮次上限也不继续。
    另外模型**不能提前结束**——只要还有未解决的高危意见，就按规则继续。
    """

    items = open_findings(findings)
    if not items:
        return PlanningDecision(
            "continue_round",
            "rules",
            "首轮复核没有未解决的中/高危问题，不追加轮次",
            {"continue": False},
        )
    if round_no >= max_rounds:
        return PlanningDecision(
            "continue_round",
            "rules",
            f"已达轮次上限 {max_rounds}，不再追加",
            {"continue": False},
        )

    summary = "；".join(f"[{item.severity.value}] {item.category}：{item.detail}" for item in items[:6])
    rules_detail = f"仍有 {len(items)} 条未解决的中/高危意见，按规则追加一轮"

    if llm is None or not getattr(llm, "available", False):
        return PlanningDecision("continue_round", "rules", rules_detail, {"continue": True})

    try:
        payload = llm.chat_json(
            ROUND_SYSTEM,
            f"当前是第 {round_no} 轮（上限 {max_rounds}）。\n未解决的问题：\n{summary}",
            max_tokens=200,
            purpose="轮次决策",
        )
        verdict = payload.get("continue")
    except Exception:  # noqa: BLE001
        return PlanningDecision(
            "continue_round", "rules", rules_detail + "（模型不可用，已回退）", {"continue": True}
        )

    reason = str(payload.get("reason") or "").strip()
    if verdict is False:
        has_high = any(item.severity == Severity.HIGH for item in items)
        if has_high:
            # 安全侧收口：高危问题还在，不接受「提前结束」
            return PlanningDecision(
                "continue_round",
                "rules",
                "模型建议结束，但仍存在未解决的高危意见，按规则继续一轮",
                {"continue": True},
            )
        return PlanningDecision(
            "continue_round",
            "llm",
            f"模型判定剩余问题不影响结论，不再追加轮次{('（' + reason + '）') if reason else ''}",
            {"continue": False},
        )
    return PlanningDecision(
        "continue_round",
        "llm",
        f"模型判定需要继续一轮{('（' + reason + '）') if reason else ''}",
        {"continue": True},
    )


# ---------------------------------------------------------------------- 路径排序
ORDER_SYSTEM = """你是维权路径规划助手，根据案情给三条路径排序。
只输出 JSON：{"order": ["negotiate", "complaint", "litigation"], "reason": "一句话理由"}
三条路径的 key 固定为 negotiate（协商）、complaint（投诉/调解）、litigation（仲裁/诉讼）。
只能排序，不得新增、删除或改名。"""


def rank_actions(
    profile: CaseProfile, assessment=None, llm=None
) -> PlanningDecision:
    """给行动路径排序。模型只能排序，不得新增路径。"""

    rules_detail = "按通用顺序给出维权路径：协商 → 投诉/调解 → 仲裁/诉讼"
    if llm is None or not getattr(llm, "available", False):
        return PlanningDecision("action_order", "rules", rules_detail, {"order": list(ACTION_KEYS)})

    amount = getattr(assessment, "legal_cap", 0) if assessment else 0
    prompt = (
        f"案由：{profile.cause}\n"
        f"争议金额约：{amount:.0f} 元\n"
        f"已提交材料：{'、'.join(profile.available_evidence) or '无'}\n"
        f"缺失材料：{'、'.join(profile.missing_evidence) or '无'}"
    )
    try:
        payload = llm.chat_json(
            ORDER_SYSTEM, prompt, max_tokens=200, purpose="维权路径排序"
        )
        raw = [str(item).strip() for item in (payload.get("order") or [])]
        reason = str(payload.get("reason") or "").strip()
    except Exception:  # noqa: BLE001
        return PlanningDecision(
            "action_order", "rules", rules_detail + "（模型不可用，已回退）", {"order": list(ACTION_KEYS)}
        )

    kept = [key for key in raw if key in ACTION_KEYS]
    order = list(dict.fromkeys(kept))
    order += [key for key in ACTION_KEYS if key not in order]  # 补全，保证是完整排列
    detail = "模型按本案情况排序：" + " → ".join(ACTION_LABELS[key] for key in order)
    if reason:
        detail += f"（{reason}）"
    return PlanningDecision("action_order", "llm", detail, {"order": order})


def render_actions(decision: PlanningDecision | None) -> str:
    """把排序结果渲染成交付物正文。"""

    order = (
        (decision.payload.get("order") if decision else None) or list(ACTION_KEYS)
    )
    order = [key for key in order if key in ACTION_TEXT] or list(ACTION_KEYS)
    lines = [
        f"{index}. {ACTION_TEXT[key]}" for index, key in enumerate(order, start=1)
    ]
    lines.append(ACTION_DISCLAIMER)
    return "\n".join(lines)


def decision_line(decision: PlanningDecision) -> str:
    """决策留痕的一行文本（报告与过程流共用同一句话）。"""

    return f"[{decision.source}] {decision.detail}"
