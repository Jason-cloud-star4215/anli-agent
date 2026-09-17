"""用大模型做案情理解，规则层负责校验与兜底。

分工：模型负责「听懂人话」，规则负责「账要算对」。
模型输出会被强制约束在已知案由、赔偿项目和证据词表内，
越界内容一律丢弃，模型调用失败时自动退回纯规则抽取。
"""

from __future__ import annotations

import os

from .intake import (
    CAUSE_KEYWORDS,
    CLAIM_RULES,
    EVIDENCE_VOCAB,
    ITEM_ALIASES,
    responsibility_applies,
    _disputed_points,
    build_profile,
    classify_cause,
    extract_evidence,
    extract_expectation,
    findings_to_profile_parts,
    infer_implied_items,
    infer_evidence_from_filenames,
    parse_responsibility,
)
from .item_assessment import assess_items, sufficiency
from .materials import cross_check_amounts, evidence_from_materials, summarize
from .models import CaseProfile
from .redact import describe, redact_with_counts

# 发送未脱敏原文的开关：默认关闭，必须由用户显式打开
RAW_TEXT_ENV = "ANLI_SEND_RAW_TEXT"

SYSTEM_PROMPT = """你是法律案情抽取助手，把用户的口语描述整理成结构化 JSON。
只输出 JSON，不要任何解释文字。

案由只能从下列取值中选择：
{causes}

每个案由可用的赔偿项目：
{items}

证据名称只能从下列取值中选择：
{evidence}

输出 JSON 结构：
{{
  "cause": "案由",
  "summary": "一句话概括纠纷",
  "responsibility_ratio": 0到1之间的小数,
  "claim_items": {{"赔偿项目名": 金额数字}},
  "evidence": [{{"name": "证据名", "have_material": true}}],
  "unquantified_items": ["用户提到但没说具体金额的赔偿项目"],
  "expectation": 期望赔偿总额数字,
  "timeline": ["按时间顺序的事件"],
  "disputed_points": ["争议焦点"]
}}

规则：
1. 金额只填用户明确说出的数字，没说的不要编造。
2. have_material 为 true 表示用户手上有原件、截图或票据；只是口头提到填 false。
3. 用户上传的文件名视为有材料。
4. 用户主张「退一赔三」但没说具体金额时，claim_items 里只填商品价款。
5. 认不出案由时 cause 填「一般民事纠纷」。
"""


def build_prompt(description: str, file_names: list[str]) -> str:
    files = "、".join(file_names) if file_names else "无"
    return f"用户上传的材料文件名：{files}\n\n用户的描述：\n{description}"


def _system_prompt() -> str:
    causes = "、".join(CAUSE_KEYWORDS.keys())
    evidence = "、".join(EVIDENCE_VOCAB.keys())
    items = "\n".join(
        f"- {cause}：{'、'.join(rules.keys())}"
        for cause, rules in CLAIM_RULES.items()
    )
    return SYSTEM_PROMPT.format(causes=causes, items=items, evidence=evidence)


def profile_from_payload(
    payload: dict,
    description: str,
    file_names: list[str],
    region: str = "",
    confirmed_evidence: list[str] | None = None,
    waived_evidence: list[str] | None = None,
    quality_answers: dict[str, list[str]] | None = None,
    waive_all: bool = False,
    material_facts: list | None = None,
) -> CaseProfile:
    """把模型输出落回受控结构；越界字段直接丢弃。"""

    cause = str(payload.get("cause") or "").strip()
    if cause not in CAUSE_KEYWORDS:
        cause = classify_cause(description + " " + " ".join(file_names))

    allowed_items = set(ITEM_ALIASES.get(cause, {})) | set(CLAIM_RULES.get(cause, {}))
    amounts: dict[str, float] = {}
    for item, value in (payload.get("claim_items") or {}).items():
        if item not in allowed_items:
            continue
        try:
            amounts[item] = float(value)
        except (TypeError, ValueError):
            continue
    amounts = infer_implied_items(cause, amounts, description)

    files_evidence = infer_evidence_from_filenames(file_names)
    strong = set(files_evidence)
    strong_from_llm: set[str] = set()
    weak: set[str] = set()
    for entry in payload.get("evidence") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if name not in EVIDENCE_VOCAB:
            continue
        if entry.get("have_material"):
            strong.add(name)
            strong_from_llm.add(name)
        else:
            weak.add(name)

    # 规则层再兜一遍，避免模型漏掉描述里写清楚的证据
    strong_text, weak_text = extract_evidence(description)
    strong |= strong_text
    weak |= weak_text - strong
    confirmed = {name for name in (confirmed_evidence or []) if name in EVIDENCE_VOCAB}
    strong |= confirmed
    # 上传材料的正文里读到的证据线索（以前只有文件名参与识别）
    material_tags = evidence_from_materials(material_facts or [])
    strong |= material_tags

    item_findings = assess_items(cause, amounts, strong, weak, quality_answers)
    supported, blocked, partial, notes = findings_to_profile_parts(cause, item_findings)

    unquantified = [
        str(item)
        for item in (payload.get("unquantified_items") or [])
        if item in allowed_items and item not in amounts
    ]
    for item, aliases in ITEM_ALIASES.get(cause, {}).items():
        if item not in amounts and item not in unquantified and any(
            alias in description for alias in aliases
        ):
            unquantified.append(item)

    ratio = payload.get("responsibility_ratio")
    if not responsibility_applies(cause):
        # 消费维权、劳动争议不按责任比例分摊：模型给的百分比一律忽略
        ratio_value = 1.0
    else:
        try:
            ratio_value = float(ratio)
            if not 0 < ratio_value <= 1:
                ratio_value = parse_responsibility(description, cause)
        except (TypeError, ValueError):
            ratio_value = parse_responsibility(description, cause)

    expectation = payload.get("expectation")
    try:
        expectation_value = float(expectation)
    except (TypeError, ValueError):
        expectation_value = extract_expectation(description)

    timeline = [str(item) for item in (payload.get("timeline") or [])][:10]
    disputed = [str(item) for item in (payload.get("disputed_points") or [])][:6]

    material_summary = summarize(material_facts or [])
    material_notes: list[str] = []
    if material_facts:
        if material_summary["total"]:
            material_notes.append(material_summary["text"])
        material_notes += cross_check_amounts(material_facts, amounts)
        material_notes += [
            f"{item.name}：{item.reason}" for item in material_facts if not item.parsed
        ]

    return CaseProfile(
        summary=str(payload.get("summary") or description[:60]),
        cause=cause,
        description=description,
        intake_source="llm",
        region=region,
        responsibility_ratio=ratio_value,
        timeline=timeline,
        disputed_points=disputed or _disputed_points(cause, blocked),
        available_evidence=sorted(strong | weak),
        missing_evidence=[
            need
            for need in sorted(
                {need for rule in CLAIM_RULES.get(cause, {}).values() for need in rule["required"]}
            )
            if need not in strong | weak
        ],
        evidence_sources={
            item: (
                "材料正文中读到"
                if item in material_tags
                else ("已上传或有材料" if item in strong else "仅口头提及")
            )
            for item in sorted(strong | weak)
        },
        evidence_notes=notes + material_notes,
        claim_items=supported,
        blocked_items=blocked,
        partial_items=partial,
        item_findings=item_findings,
        extra={
            "expectation": expectation_value,
            "unquantified_items": unquantified,
            "waived_evidence": sorted(
                {name for name in (waived_evidence or []) if name in EVIDENCE_VOCAB}
            ),
            "quality_answers": dict(quality_answers or {}),
            "sufficiency": sufficiency(item_findings),
            "responsibility_applies": responsibility_applies(cause),
            "waive_all": bool(waive_all),
        },
    )


def build_profile_llm(
    description: str,
    file_names: list[str] | None = None,
    llm=None,
    region: str = "",
    confirmed_evidence: list[str] | None = None,
    waived_evidence: list[str] | None = None,
    quality_answers: dict[str, list[str]] | None = None,
    waive_all: bool = False,
    material_facts: list | None = None,
) -> CaseProfile:
    """优先用大模型理解案情，失败则退回规则抽取。

    对外发送前**一律先脱敏**：案情描述里常有手机号、身份证、详细住址，
    这些没有理由离开本机。用户显式设置 `ANLI_SEND_RAW_TEXT=1` 才发送原文。
    规则版是本机计算，不需要脱敏，因此仍用用户原话。
    """

    file_names = file_names or []
    if llm is None or not getattr(llm, "available", False):
        return build_profile(
            description,
            file_names,
            region,
            confirmed_evidence,
            waived_evidence,
            quality_answers,
            waive_all=waive_all,
            material_facts=material_facts,
        )

    send_raw = os.environ.get(RAW_TEXT_ENV) == "1"
    payload_text, redactions = (description, {}) if send_raw else redact_with_counts(description)
    try:
        payload = llm.chat_json(_system_prompt(), build_prompt(payload_text, file_names))
        profile = profile_from_payload(
            payload,
            # 报告里保留用户原话（本地产物）；对外发送的只是脱敏版
            description,
            file_names,
            region,
            confirmed_evidence,
            waived_evidence,
            quality_answers,
            waive_all=waive_all,
            material_facts=material_facts,
        )
    except Exception:  # noqa: BLE001 - 模型不可用时不能拖垮主流程
        profile = build_profile(
            description,
            file_names,
            region,
            confirmed_evidence,
            waived_evidence,
            quality_answers,
            waive_all=waive_all,
            material_facts=material_facts,
        )
    profile.extra["sent_raw_text"] = send_raw
    profile.extra["redaction_note"] = (
        "本次对外发送包含原文（你已用 ANLI_SEND_RAW_TEXT=1 明确允许）"
        if send_raw
        else describe(redactions)
    )
    return profile
