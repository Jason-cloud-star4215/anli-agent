"""策略建议：把评估结果翻译成按角色、按优先级排序的行动方案。

同一个案子，主张方、抗辩方、法官视角该做的事完全不同：
- 主张方：把损失做实、把金额顶上去、选成本最低的路径。
- 抗辩方：找对方证据和计算的问题，把责任和金额压下来。
- 法官视角：归纳争点、分配举证责任、看调解空间。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evidence_chain import potential_cap
from .models import CaseProfile, FeasibilityAssessment, Role


def format_range(low: float | None, high: float | None) -> str:
    """区间展示：上限等于下限时显示单值，避免出现「899–899」这种写法。"""

    if low is None or high is None:
        return "无数据"
    if round(low) == round(high):
        return f"约 {low:.0f}"
    return f"{low:.0f}–{high:.0f}"


@dataclass
class Strategy:
    priority: str  # P0 立刻做 / P1 建议做 / P2 视情况
    action: str
    reason: str
    expected: str
    materials: list[str] = field(default_factory=list)


def build_strategies(
    profile: CaseProfile,
    assessment: FeasibilityAssessment,
    blocked_items: dict[str, dict[str, object]],
    limitation_days: int | None = None,
    role: Role = Role.CLAIMANT,
) -> list[Strategy]:
    strategies: list[Strategy] = []
    strategies += _amount_strategies(profile, assessment, blocked_items, role)
    if limitation_days is not None and limitation_days <= 180:
        strategies.append(
            Strategy(
                priority="P0",
                action=(
                    "立即启动主张程序并保留书面催告记录"
                    if role == Role.CLAIMANT
                    else "核对对方主张是否已过时效，作为第一顺位抗辩"
                ),
                reason=f"距时效届满约 {limitation_days} 天",
                expected=(
                    "避免因时效问题丧失胜诉权"
                    if role == Role.CLAIMANT
                    else "若已过时效且你提出抗辩，对方请求可能被驳回"
                ),
                materials=["书面催告记录", "送达凭证"],
            )
        )
    strategies += _path_strategies(profile, assessment, role)
    return strategies


# --------------------------------------------------------------------- 金额与证据
def _amount_strategies(
    profile: CaseProfile,
    assessment: FeasibilityAssessment,
    blocked_items: dict[str, dict[str, object]],
    role: Role,
) -> list[Strategy]:
    out: list[Strategy] = []

    for item in profile.extra.get("unquantified_items") or []:
        if role == Role.CLAIMANT:
            out.append(
                Strategy(
                    priority="P0",
                    action=f"补充{item}的具体金额",
                    reason=f"你提到了{item}，但没有给出金额，系统无法把它计入损失",
                    expected="补齐金额后这部分才能进入主张范围",
                    materials=[f"{item}的凭证或计算依据"],
                )
            )
        elif role == Role.DEFENDANT:
            out.append(
                Strategy(
                    priority="P1",
                    action=f"要求对方就{item}给出金额和计算依据",
                    reason=f"对方只提{item}却没给金额，说明这部分主张尚未成立",
                    expected="对方举证不能的部分不应计入赔偿",
                    materials=["举证通知书回复", "质证意见"],
                )
            )
        else:
            out.append(
                Strategy(
                    priority="P1",
                    action=f"要求双方就{item}补充金额与计算依据",
                    reason="该项有主张无金额，事实尚未查清",
                    expected="查清后该项才能进入裁判范围",
                )
            )

    ranked = sorted(
        blocked_items.items(),
        key=lambda pair: float(pair[1].get("amount") or 0),
        reverse=True,
    )
    for item, info in ranked:
        amount = float(info.get("amount") or 0)
        if amount <= 0:
            continue
        kind = str(info.get("kind") or "unsupported")
        params = [str(name) for name in (info.get("params") or [])]
        missing = "、".join(
            params or [str(name) for name in (info.get("missing") or [])]
        )
        if kind == "standard":
            out.append(
                Strategy(
                    priority="P0" if amount >= 1000 else "P1",
                    action=f"补齐{missing}，按法定标准主张{item}（约 {amount:.0f} 元）",
                    reason=f"{info.get('basis') or '该项缺凭证'}；法院会按法定标准计算，而不是直接不支持",
                    expected="补齐参数后该项可按标准计算，进入可支持金额",
                    materials=params,
                )
            )
            continue
        if role == Role.CLAIMANT:
            out.append(
                Strategy(
                    priority="P0" if amount >= 1000 else "P1",
                    action=f"补齐{missing}，把{item}计入损失",
                    reason=f"目前缺{missing}，{item} 的 {amount:.0f} 元可能全部不予支持（不是打折扣，是归零）",
                    expected=f"补上材料后这 {amount:.0f} 元才有机会进入可支持金额",
                    materials=[str(name) for name in (info.get("missing") or [])],
                )
            )
        elif role == Role.DEFENDANT:
            out.append(
                Strategy(
                    priority="P0" if amount >= 1000 else "P1",
                    action=f"要求对方出示{missing}，否则不认可 {item} {amount:.0f} 元",
                    reason="该项缺证据支撑，举证不能的后果应由主张方承担",
                    expected=f"若对方拿不出，这 {amount:.0f} 元可能不被支持",
                    materials=["质证意见", "证据目录"],
                )
            )
        else:
            out.append(
                Strategy(
                    priority="P1",
                    action=f"就{item}（{amount:.0f} 元）组织举证与质证",
                    reason=f"该项缺{missing}，属于需要查明的事实",
                    expected="明确该项目是否具备证据基础",
                    materials=[str(name) for name in (info.get("missing") or [])],
                )
            )

    # 预期校准放在最后：一项都撑不起来时，先补证据比谈金额更重要
    if assessment.legal_cap <= 0 and role == Role.CLAIMANT:
        out.insert(
            0,
            Strategy(
                priority="P0",
                action="先补齐关键材料，再谈赔偿金额",
                reason="目前没有任何一项有材料支撑，按证据规则这些主张可能全部不予支持，谈金额没有意义",
                expected="补齐材料后才有可支持金额，谈判才有基础",
            ),
        )
        return out

    if assessment.likely_high is None:
        return out
    if role == Role.DEFENDANT:
        if assessment.expectation > assessment.likely_high:
            out.append(
                Strategy(
                    priority="P1",
                    action=f"把可接受的赔偿上限锚定在 {assessment.likely_high:.0f} 元左右再谈",
                    reason=(
                        f"对方主张 {assessment.expectation:.0f} 元，但按现有证据与同类判决"
                        f"可能只支持到 {assessment.likely_high:.0f} 元，超出部分不必接受"
                    ),
                    expected="把谈判起点从对方主张拉回到判决区间，减少不必要的赔付",
                )
            )
    elif role == Role.JUDGE:
        if assessment.expectation > assessment.likely_high:
            out.append(
                Strategy(
                    priority="P1",
                    action=f"以 {assessment.likely_high:.0f} 元作为调解与裁判的参照上限",
                    reason=(
                        f"标的 {assessment.expectation:.0f} 元，但按现有证据与同类判决"
                        f"只支持到约 {assessment.likely_high:.0f} 元，差额部分需要向双方释明"
                    ),
                    expected="把双方预期拉回证据范围内，提高调解成功率",
                )
            )
    elif assessment.expectation > assessment.likely_high:
        out.append(
            Strategy(
                priority="P1",
                action=(
                    f"把索赔预期从 {assessment.expectation:.0f} 元调整到 "
                    f"{assessment.likely_high:.0f} 元左右"
                ),
                reason="按现有证据和同类判决，超出部分很难获得支持，坚持高诉求反而会拉长协商周期",
                expected="协商更容易达成，避免因预期过高而白跑程序",
            )
        )
    return out


# --------------------------------------------------------------------- 路径与打法
def _path_strategies(
    profile: CaseProfile, assessment: FeasibilityAssessment, role: Role
) -> list[Strategy]:
    cause = profile.cause
    cap = assessment.likely_high
    out: list[Strategy] = []

    if cause == "交通事故人身损害赔偿":
        if role == Role.CLAIMANT:
            out.append(
                Strategy(
                    priority="P0",
                    action="先向对方保险公司索赔，再考虑诉讼",
                    reason="交强险在责任限额内不按责任比例赔付，商业险按合同赔，保险先行通常比诉讼更快拿到钱",
                    expected="多数损失可在理赔阶段解决，诉讼只作兜底",
                    materials=["事故认定书", "医疗费票据", "损失清单"],
                )
            )
            if "伤残鉴定" not in profile.available_evidence:
                out.append(
                    Strategy(
                        priority="P1",
                        action="评估是否构成伤残并申请鉴定",
                        reason="构成伤残才能主张残疾赔偿金和精神损害抚慰金，通常是金额落差最大的一块",
                        expected="如构成伤残，赔偿总额可能明显提高",
                        materials=["病历", "出院小结", "影像资料"],
                    )
                )
        elif role == Role.DEFENDANT:
            out.append(
                Strategy(
                    priority="P0",
                    action="先让保险在交强险限额内赔付，不要自己先掏钱",
                    reason="交强险在责任限额内不按责任比例赔，自掏腰包再找保险理赔容易吃亏",
                    expected="由保险先行承担大部分赔偿，降低个人支出",
                    materials=["保险单", "事故认定书"],
                )
            )
            out.append(
                Strategy(
                    priority="P0",
                    action="针对医疗费关联性、误工费基数、护理费必要性逐项质证",
                    reason="赔偿项目需与事故有因果关系且证据充分，非医保用药、与事故无关的旧病、无医嘱的营养费都可能被核减",
                    expected="可核减对方主张中缺乏依据的部分",
                    materials=["病历对照清单", "误工证明与流水", "用药清单"],
                )
            )
            out.append(
                Strategy(
                    priority="P1",
                    action="核对责任认定比例，必要时在复核期内申请复核",
                    reason="责任比例直接决定赔付比例，认定书不是不可推翻",
                    expected="下调责任比例即等额下调赔偿金额",
                    materials=["事故认定书", "现场照片", "行车记录"],
                )
            )
        else:
            out.append(
                Strategy(
                    priority="P0",
                    action="归纳争点：责任比例、损失范围、证据证明力",
                    reason="交通事故案件的裁判重点集中在责任划分与损失认定",
                    expected="明确审理方向，避免双方在无关事实上来回争",
                )
            )
            out.append(
                Strategy(
                    priority="P1",
                    action="组织调解，以类案区间作为调解参照",
                    reason="同类案件的赔偿区间可作为双方预期锚点，调解结案效率更高",
                    expected="在区间内促成调解，减少上诉风险",
                )
            )

    elif cause == "消费维权":
        if role == Role.CLAIMANT:
            out.append(
                Strategy(
                    priority="P0",
                    action="先在平台投诉并向 12315 举报，再决定是否起诉",
                    reason="投诉成本低、周期短，商家在平台介入后和解概率较高",
                    expected="多数消费纠纷在投诉阶段即可退款或赔偿",
                    materials=["订单截图", "商品宣传页面", "聊天记录"],
                )
            )
        elif role == Role.DEFENDANT:
            out.append(
                Strategy(
                    priority="P0",
                    action="判断是否构成欺诈：区分夸大宣传与故意告知虚假情况",
                    reason="三倍惩罚性赔偿以欺诈为要件，仅属宣传用语夸张、不影响主要功能的，通常不认定欺诈",
                    expected="若欺诈不成立，三倍赔偿部分可全部抗掉，最多退货退款",
                    materials=["商品详情页", "质检报告", "同类商品宣传对比"],
                )
            )
            out.append(
                Strategy(
                    priority="P1",
                    action="主动提出退货退款或换货方案并保留协商记录",
                    reason="先行提出合理补救可降低被认定欺诈的风险，也有利于从轻处理",
                    expected="把争议控制在退款层面，避免三倍赔偿",
                )
            )
        else:
            out.append(
                Strategy(
                    priority="P0",
                    action="审查欺诈构成要件：虚假陈述、主观故意、因果关系",
                    reason="惩罚性赔偿适用条件严格，需逐项查明",
                    expected="确定是否支持三倍赔偿",
                )
            )

    elif cause == "劳动争议":
        if role == Role.CLAIMANT:
            out.append(
                Strategy(
                    priority="P0",
                    action="先申请劳动仲裁（诉讼前置程序）",
                    reason="劳动争议必须先仲裁，直接起诉法院不会受理",
                    expected="仲裁阶段即可解决大部分争议",
                    materials=["劳动合同", "工资流水", "解除通知"],
                )
            )
        elif role == Role.DEFENDANT:
            out.append(
                Strategy(
                    priority="P0",
                    action="准备解除合法性的证据，用人单位承担举证责任",
                    reason="解除劳动合同的合法性由用人单位举证，举证不能将承担不利后果",
                    expected="举证充分可避免被认定违法解除的二倍赔偿",
                    materials=["规章制度", "考勤记录", "警告与送达凭证"],
                )
            )
        else:
            out.append(
                Strategy(
                    priority="P0",
                    action="明确举证责任分配：解除合法性由用人单位举证",
                    reason="举证责任分配直接决定审理结果",
                    expected="围绕举证责任组织庭审",
                )
            )

    if cap is not None and cap <= 10000:
        if role == Role.CLAIMANT:
            out.append(
                Strategy(
                    priority="P1",
                    action="优先协商或调解，把诉讼作为最后手段",
                    reason="标的额较小时，诉讼的时间成本和费用占比偏高",
                    expected="用更低成本拿到接近区间上限的结果",
                )
            )
        elif role == Role.DEFENDANT:
            out.append(
                Strategy(
                    priority="P1",
                    action="用诉讼成本说服对方接受一次性调解方案",
                    reason="标的额不大，对方走完诉讼的时间与费用成本可能不划算",
                    expected="以较小金额一次性了结，避免长期牵扯",
                )
            )
    return out


# --------------------------------------------------------------------- 结论摘要
def render_summary(
    profile: CaseProfile,
    assessment: FeasibilityAssessment,
    strategies: list[Strategy],
    role: Role = Role.CLAIMANT,
) -> str:
    """一段话结论，放在报告最前面，措辞随身份变化。"""

    label = {
        Role.CLAIMANT: "你的诉求",
        Role.DEFENDANT: "对方的索赔",
        Role.JUDGE: "本案标的",
    }[role]

    if assessment.likely_high is None:
        conclusion = "同类案例样本不足，暂时只能给出法定层面的判断"
        numbers = f"{label}为 {assessment.expectation:.0f} 元，但缺少可比案例，无法判断现实落点。"
    else:
        case_text = format_range(assessment.case_low, assessment.case_high)
        likely_text = format_range(assessment.likely_low, assessment.likely_high)
        if role == Role.DEFENDANT:
            if assessment.expectation > assessment.likely_high:
                conclusion = "对方的主张明显偏高，你有抗辩空间"
            else:
                conclusion = "对方的主张基本在合理区间内，抗辩空间有限"
            numbers = (
                f"对方主张 {assessment.expectation:.0f} 元，按现有证据与责任比例，"
                f"可能被支持的约 {assessment.legal_cap:.0f} 元；同类案例通常判 {case_text} 元"
                f"（中位数 {assessment.case_median:.0f} 元，样本 {assessment.sample_size} 件），"
                f"你的合理赔付预期{likely_text} 元。"
            )
        elif role == Role.JUDGE:
            conclusion = "本案事实与证据的争点已经比较清楚"
            numbers = (
                f"标的 {assessment.expectation:.0f} 元，按现有证据可支持 {assessment.legal_cap:.0f} 元；"
                f"同类案例通常判 {case_text} 元（中位数 {assessment.case_median:.0f} 元，"
                f"样本 {assessment.sample_size} 件），合理裁判区间{likely_text} 元。"
            )
        else:
            if assessment.expectation_supported:
                conclusion = "你的诉求在合理范围内"
            else:
                conclusion = "你的诉求偏高"
            if assessment.legal_cap <= 0:
                numbers = (
                    f"你提出 {assessment.expectation:.0f} 元，但目前每一项都没有材料支撑，"
                    f"按证据规则可支持金额为 0 元；同类案件在证据齐备时通常判 {case_text} 元"
                    f"（中位数 {assessment.case_median:.0f} 元，样本 {assessment.sample_size} 件），"
                    "也就是说差距出在材料，不在诉求本身。"
                )
            else:
                numbers = (
                    f"你提出 {assessment.expectation:.0f} 元，按现有证据可支持 {assessment.legal_cap:.0f} 元；"
                    f"同类案件在证据齐备时通常判 {case_text} 元（中位数 {assessment.case_median:.0f} 元，"
                    f"样本 {assessment.sample_size} 件），受你的证据和责任比例限制，"
                    f"最可能落点{likely_text} 元。"
                )

    gap_lines = []
    for reason in assessment.gap_reasons:
        gap_lines.append(reason)
    gap = ("主要问题在于：" + "；".join(gap_lines) + "。") if gap_lines else ""

    top = strategies[0].action if strategies else "先补齐关键证据"

    extra_lines: list[str] = []
    # 类案口径必须写在结论里：区间来自哪个地区、有没有演示数据，不能藏在附录
    if assessment.sample_size:
        extra_lines.append("类案口径：" + assessment.sample_note + "。")
    if assessment.has_synthetic:
        extra_lines.append(
            "注意：本次类案区间包含演示数据，仅用于演示检索与计算流程，"
            "不是真实裁判结果，不作为法律依据。"
        )
    if assessment.cross_region_sample_size and assessment.region:
        extra_lines.append(
            f"其中 {assessment.cross_region_sample_size} 件来自其他省份，"
            "各地裁判尺度存在差异，跨省区间只能作弱参照。"
        )
    cap_if_full = potential_cap(profile) * profile.responsibility_ratio
    if cap_if_full > assessment.legal_cap + 1:
        extra_lines.append(
            f"两种口径：材料补齐后最高可主张约 {cap_if_full:.0f} 元；"
            f"按你目前手上有的材料，可支持约 {assessment.legal_cap:.0f} 元。"
        )
    waived = (profile.extra or {}).get("waived_evidence") or []
    if waived:
        extra_lines.append(
            "你已说明这些材料暂时拿不出来：" + "、".join(waived)
            + "。相关金额按保守口径计算，后续取得材料可以重新算。"
        )

    return "\n".join(
        [line for line in [
            f"一句话结论：{conclusion}。",
            numbers,
            gap,
            *extra_lines,
            f"建议的第一步：{top}。",
            "本结论基于你提供的描述与材料，属于参考性质，重大金额或时效临近请咨询执业律师。",
        ] if line]
    )
