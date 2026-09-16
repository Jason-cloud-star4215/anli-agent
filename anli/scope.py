"""范围闸门与转人工闸门。

对应 PRD 第 6 节业务规则 9、第 8 节异常场景，以及 CLAUDE.md 的红线：

- 不得为刑事案件、行政案件提供分析结论，识别到就直接停并给求助渠道；
- 时效临近、金额重大、涉人身安全要转人工（NEEDS_HUMAN），先给事实与风险提示，
  用户确认后才给行动方案与文书草稿。

判定全部是确定性规则，不交给模型，避免「模型觉得可以答」就越过红线。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 金额重大阈值：普通民事纠纷里超过这个数，建议必须咨询执业律师
MAJOR_AMOUNT = 500_000.0
# 时效临近阈值：剩余不足这个天数就转人工
LIMITATION_ALERT_DAYS = 90


@dataclass
class OutOfScope:
    """超出覆盖范围（刑事 / 行政）。"""

    kind: str
    matched: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"超出覆盖范围：{self.kind}案件"

    def render(self) -> str:
        lines = [
            f"你描述的情况更像是{self.kind}案件，不在本产品的覆盖范围内。",
            "本产品只做民事纠纷的学习与参考，不出具刑事、行政案件的分析结论。",
            "这不是能力问题，而是这类案件的结果高度依赖案卷、程序期限和专业辩护，"
            "用通用分析代替会给你错误的确定性。",
            "",
            "建议走这些渠道：",
        ]
        lines += [f"· {item}" for item in self.channels]
        if self.matched:
            lines += ["", "触发判断的说法（可核对）：" + "、".join(self.matched[:5])]
        return "\n".join(lines)


CRIMINAL_KEYWORDS = [
    "刑事", "判刑", "坐牢", "取保候审", "看守所", "刑事拘留", "逮捕", "检察院",
    "盗窃罪", "诈骗罪", "故意伤害罪", "寻衅滋事", "醉驾", "危险驾驶罪",
    "被立案", "立案侦查", "刑事立案", "刑事附带民事", "缓刑", "量刑",
]

ADMIN_KEYWORDS = [
    "行政处罚", "行政复议", "行政诉讼", "行政拘留", "行政处罚决定书",
    "违章处罚", "城管执法", "吊销许可证", "责令停产停业",
    "不服处罚", "民告官", "政府信息公开",
]

CRIMINAL_CHANNELS = [
    "报警 / 控告：拨打 110，或到属地公安机关报案并索取受理回执",
    "法律咨询与援助：12348 公共法律服务热线；经济困难可向属地法律援助中心申请免费律师",
    "已进入刑事程序：尽快委托辩护律师；控告申诉、司法救助可拨 12309 检察服务热线",
    "涉及人身安全：可向公安机关申请人身安全保护措施",
]

ADMIN_CHANNELS = [
    "行政复议：收到决定书起 60 日内，向作出决定机关的本级政府或上一级主管部门申请",
    "行政诉讼：一般 6 个月内向有管辖权的法院起诉，先核对是否属于复议前置",
    "咨询：12348 公共法律服务热线；程序不熟的建议委托律师",
    "投诉举报与协调：12345 政务服务便民热线",
]

# 涉人身安全或严重后果的说法：命中即转人工
HUMAN_RISK_KEYWORDS = [
    "威胁", "恐吓", "殴打", "打人", "家暴", "家庭暴力", "骚扰", "跟踪", "报复",
    "自杀", "轻生", "死亡", "重伤", "住院", "伤残", "肇事逃逸", "逃逸",
    "孕妇", "未成年", "老人受伤", "精神损害",
]

# 转人工时统一给的求助渠道
HUMAN_CHANNELS = [
    "法律咨询：12348 公共法律服务热线（免费）",
    "法律援助：属地司法局法律援助中心，经济困难可申请免费代理",
    "律师协会：可通过属地律师协会查询执业律师与专业领域",
    "涉及人身安全：立即报警（110），必要时申请人身安全保护令",
]


def detect_out_of_scope(text: str) -> OutOfScope | None:
    """识别刑事 / 行政案件；普通民事案件返回 None。"""

    raw = text or ""
    criminal = [word for word in CRIMINAL_KEYWORDS if word in raw]
    administrative = [word for word in ADMIN_KEYWORDS if word in raw]
    if criminal and len(criminal) >= len(administrative):
        return OutOfScope(kind="刑事", matched=criminal, channels=CRIMINAL_CHANNELS)
    if administrative:
        return OutOfScope(kind="行政", matched=administrative, channels=ADMIN_CHANNELS)
    return None


def human_review_reasons(
    description: str,
    expectation: float,
    limitation_days: int | None = None,
) -> list[str]:
    """需要转人工的理由；空列表表示可以正常交付。"""

    reasons: list[str] = []
    raw = description or ""

    if expectation and expectation >= MAJOR_AMOUNT:
        reasons.append(
            f"标的金额 {expectation:.0f} 元，属于金额重大情形，"
            "金额判断与实际执行都建议由执业律师复核"
        )

    if limitation_days is not None and limitation_days <= LIMITATION_ALERT_DAYS:
        reasons.append(
            f"按内置时效规则估算，距时效届满仅约 {limitation_days} 天，"
            "一旦过期可能丧失胜诉权，务必尽快处理"
        )

    hits = [word for word in HUMAN_RISK_KEYWORDS if word in raw]
    if hits:
        reasons.append(
            "描述中出现涉及人身安全或严重后果的表述（"
            + "、".join(hits[:4])
            + "），这类情况建议由专业人士介入"
        )

    return reasons


def human_review_artifact(reasons: list[str]) -> str:
    """转人工时给人的说明：为什么停在这里、去哪儿求助、为什么先不给行动方案。"""

    lines = [
        "系统在这里停住了。原因不是分析不出来，而是这类情况不该由工具直接给行动结论。",
        "",
        "触发原因：",
    ]
    lines += [f"· {reason}" for reason in reasons]
    lines += [
        "",
        "建议求助渠道：",
    ]
    lines += [f"· {item}" for item in HUMAN_CHANNELS]
    lines += [
        "",
        "在页面上确认「我已知晓风险，继续查看完整方案」之前，系统只给事实与数字部分"
        "（结论摘要、可行性评估、证据清单、金额计算），不给行动路径与文书草稿——"
        "这两类内容会直接影响你的下一步决策，需要你确认风险之后再给。",
    ]
    return "\n".join(lines)
