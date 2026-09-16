"""诉求可行性评估。

把用户的主观期望，翻译成「法定上限 / 类案现实区间 / 最可能落点」三段式结论。
对应 PRD 第 5 节 P0 第 4 项与 BACKEND_STRUCTURE.md 第 5.5 节。
"""

from __future__ import annotations

import statistics

from .models import CaseRecord, FeasibilityAssessment

MIN_SAMPLE = 3
# 证据闸门：充分度低于这个值，或未计入金额占比超过另一条线，
# 就不给结论性的赔付数字，只给「补齐后可参考」的区间
MIN_SUFFICIENCY = 0.7
MAX_BLOCKED_SHARE = 0.4


def amount_range(amounts: list[float]) -> tuple[float | None, float | None, float | None]:
    """返回 (下四分位, 中位数, 上四分位)；样本不足时返回中位数与前后的粗略范围。"""

    if not amounts:
        return None, None, None
    ordered = sorted(amounts)
    if len(ordered) == 1:
        only = ordered[0]
        return only, only, only
    if len(ordered) < 4:
        return ordered[0], statistics.median(ordered), ordered[-1]
    quartiles = statistics.quantiles(ordered, n=4, method="inclusive")
    return quartiles[0], quartiles[1], quartiles[2]


def _round(value: float | None, digits: int = 0) -> float | None:
    return None if value is None else round(value, digits)


def build_assessment(
    expectation: float,
    legal_cap: float,
    cases: list[CaseRecord],
    responsibility_ratio: float = 1.0,
    gap_reasons: list[str] | None = None,
    uplift_paths: list[str] | None = None,
    expectation_label: str = "期望金额",
    region: str = "",
    sufficiency: float = 1.0,
    blocked_amount: float = 0.0,
    claimed_amount: float = 0.0,
) -> FeasibilityAssessment:
    """生成诉求可行性评估。

    地域口径：同样是民事纠纷，各省裁判尺度不同，因此
    - 本地区样本够（≥ MIN_SAMPLE）时，只用本地区样本算区间；
    - 本地区样本不够时，才退回全体样本，并在结论里说明混入了外省案例。
    """

    usable = [case for case in cases if case.total_amount > 0]
    local = [case for case in usable if region and case.region == region]
    chosen = local if len(local) >= MIN_SAMPLE else usable
    case_amounts = [case.total_amount for case in chosen]
    sample_size = len(case_amounts)
    region_sample_size = sum(1 for case in chosen if region and case.region == region)
    cross_region_sample_size = sum(1 for case in chosen if region and case.region != region)
    synthetic_sample_size = sum(1 for case in chosen if case.synthetic)
    low, median, high = amount_range(case_amounts)
    low_sample = sample_size < MIN_SAMPLE

    cap = legal_cap * responsibility_ratio

    if low is None or high is None:
        likely_low: float | None = None
        likely_high: float | None = None
    elif cap <= 0:
        # 一项都撑不起来时，现实落点就是 0，不能拿同类案例的金额来暗示能拿到钱
        likely_low = 0.0
        likely_high = 0.0
    else:
        likely_low = min(low, cap) if cap else low
        likely_high = max(min(high, cap), likely_low)

    reasons = list(gap_reasons or [])
    if sample_size == 0:
        reasons.append("未检索到同类案例，无法给出实践区间")
    elif low_sample:
        reasons.append(f"同类案例仅 {sample_size} 件，样本不足，区间参考价值有限")
    if region and cross_region_sample_size:
        reasons.append(
            f"{region}本地区样本仅 {region_sample_size} 件，达不到 {MIN_SAMPLE} 件，"
            f"区间混入了其他省份 {cross_region_sample_size} 件，各地裁判尺度可能不同"
        )
    if synthetic_sample_size:
        reasons.append(
            f"区间数据中有 {synthetic_sample_size} 件为演示数据，仅用于演示检索与计算流程"
        )
    blocked_share = (blocked_amount / claimed_amount) if claimed_amount > 0 else 0.0
    provisional = sufficiency < MIN_SUFFICIENCY or blocked_share >= MAX_BLOCKED_SHARE
    if provisional:
        reasons.append(
            f"证据充分度 {sufficiency:.0%}，约 {blocked_amount:.0f} 元因缺证据未计入；"
            "证据补齐前系统不给结论性的赔付数字"
        )
    if expectation > cap:
        reasons.append(f"{expectation_label}超过按现有证据计算的法定上限 {_round(cap)} 元")

    return FeasibilityAssessment(
        expectation=round(expectation, 2),
        legal_cap=_round(cap, 2) or 0.0,
        case_low=_round(low, 2),
        case_median=_round(median, 2),
        case_high=_round(high, 2),
        sample_size=sample_size,
        low_sample=low_sample,
        likely_low=_round(likely_low, 2),
        likely_high=_round(likely_high, 2),
        gap_reasons=reasons,
        uplift_paths=list(uplift_paths or []),
        region=region,
        region_sample_size=region_sample_size,
        cross_region_sample_size=cross_region_sample_size,
        synthetic_sample_size=synthetic_sample_size,
        sufficiency=round(sufficiency, 3),
        blocked_amount=round(blocked_amount, 2),
        claimed_amount=round(claimed_amount, 2),
        provisional=provisional,
    )
