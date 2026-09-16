"""领域模型。

对应 BACKEND_STRUCTURE.md 第 3 节「核心数据模型」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    """主流程状态 + 分支状态，对应 PRD 第 7 节。"""

    RECEIVED = "RECEIVED"
    CLARIFYING = "CLARIFYING"
    CLASSIFIED = "CLASSIFIED"
    RESEARCHING = "RESEARCHING"
    DEBATING = "DEBATING"
    CRITIQUING = "CRITIQUING"
    EVALUATING = "EVALUATING"
    DELIVERED = "DELIVERED"
    NEEDS_INFO = "NEEDS_INFO"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Role(str, Enum):
    """用户身份与智能体角色。"""

    CLAIMANT = "claimant"
    DEFENDANT = "defendant"
    JUDGE = "judge"
    RESEARCHER = "researcher"
    REDTEAM = "redteam"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class LegalProvision:
    """结构化法条。只有 verified=True 的条文可以进入交付物。

    domain 用来隔离检索域：民事（civil）/ 刑事（criminal）/ 行政（administrative）。
    法条库允许收刑事、行政条文（拦停时能给出准确依据），
    但民事检索只会在 civil 域里搜，避免刑事条文混进民事引用。
    """

    provision_id: str
    law_name: str
    article: str
    text: str
    source_url: str = ""
    effective: str = ""
    verified: bool = False
    note: str = ""
    topics: list[str] = field(default_factory=list)
    domain: str = "civil"

    @property
    def label(self) -> str:
        return f"《{self.law_name}》{self.article}"


@dataclass
class CaseRecord:
    """类案记录。synthetic=True 表示演示数据，不能作为最终法律依据。"""

    case_id: str
    court: str
    year: int
    region: str
    case_type: str
    dispute: str
    holding: str
    amounts: dict[str, float] = field(default_factory=dict)
    evidence_points: list[str] = field(default_factory=list)
    source_url: str = ""
    synthetic: bool = False
    # 真实来源的溯源信息：官方来源名称、案号、抓取时间、关键词、金额描述
    source_name: str = ""
    case_no: str = ""
    fetched_at: str = ""
    keywords: list[str] = field(default_factory=list)
    amounts_note: str = ""

    @property
    def total_amount(self) -> float:
        return float(sum(self.amounts.values()))

    @property
    def label(self) -> str:
        return f"{self.case_id}（{self.court}，{self.year}）"


@dataclass
class Citation:
    """一条引用的出处与核验结果。"""

    source_type: str  # law | case | material
    source_id: str
    label: str
    quote: str
    verified: bool = False
    grade: str = "unverified"  # authoritative | synthetic | unverified
    note: str = ""


@dataclass
class LawSourceCheck:
    """法条在官方库（国家法律法规数据库）的回源核对结果。

    只回答「这部法在官方库是否存在、公布/施行日期、官方详情页在哪」，
    不代替条文正文核验——正文没拿到，就不能当作可引用的原文。
    """

    law_name: str
    found: bool = False
    official_title: str = ""
    publish_date: str = ""
    effective_date: str = ""
    organ: str = ""
    detail_url: str = ""
    fetched_at: str = ""
    from_cache: bool = False
    error: str = ""

    @property
    def summary(self) -> str:
        if self.error:
            return f"官方库未核对（{self.error}）"
        if not self.found:
            return "官方库未检索到该法"
        dates = []
        if self.publish_date:
            dates.append(f"公布 {self.publish_date}")
        if self.effective_date:
            dates.append(f"施行 {self.effective_date}")
        tail = ("，" + "、".join(dates)) if dates else ""
        return f"官方库已收录{tail}"


@dataclass
class CriticFinding:
    """质证复核（红队）发现的一条漏洞。"""

    category: str
    severity: Severity
    detail: str
    basis: str
    resolved: bool = False


@dataclass
class FeasibilityAssessment:
    """诉求可行性评估，产品最核心的交付物。"""

    expectation: float
    legal_cap: float
    case_low: float | None
    case_median: float | None
    case_high: float | None
    sample_size: int
    low_sample: bool
    likely_low: float | None
    likely_high: float | None
    gap_reasons: list[str] = field(default_factory=list)
    uplift_paths: list[str] = field(default_factory=list)
    # 地域与样本构成：区间到底来自哪里，必须能说清楚
    region: str = ""
    region_sample_size: int = 0
    cross_region_sample_size: int = 0
    synthetic_sample_size: int = 0
    # 证据闸门：证据不充分时不给结论性赔付数字，只给「补齐后可参考」
    sufficiency: float = 1.0
    blocked_amount: float = 0.0
    claimed_amount: float = 0.0
    provisional: bool = False

    @property
    def expectation_supported(self) -> bool:
        if self.likely_high is None:
            return False
        return self.expectation <= self.likely_high

    @property
    def has_synthetic(self) -> bool:
        return self.synthetic_sample_size > 0

    @property
    def conclusion_ready(self) -> bool:
        """证据是否足以支撑结论性数字。"""

        return not self.provisional

    @property
    def sample_note(self) -> str:
        """样本构成说明：地区 + 演示数据 + 样本量。"""

        parts: list[str] = []
        if self.region:
            parts.append(f"本地区（{self.region}）{self.region_sample_size} 件")
        if self.cross_region_sample_size:
            parts.append(f"其他省份 {self.cross_region_sample_size} 件")
        if self.synthetic_sample_size:
            parts.append(f"其中演示数据 {self.synthetic_sample_size} 件")
        if not parts:
            return f"样本 {self.sample_size} 件"
        return f"样本 {self.sample_size} 件（" + "、".join(parts) + "）"


@dataclass
class Artifact:
    """一项交付物。"""

    kind: str
    title: str
    body: str


@dataclass
class CaseProfile:
    """案情结构化结果。"""

    summary: str
    cause: str
    description: str = ""
    intake_source: str = "rules"  # rules | llm
    region: str = ""
    responsibility_ratio: float = 1.0
    timeline: list[str] = field(default_factory=list)
    disputed_points: list[str] = field(default_factory=list)
    available_evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    evidence_sources: dict[str, str] = field(default_factory=dict)
    evidence_notes: list[str] = field(default_factory=list)
    claim_items: dict[str, float] = field(default_factory=dict)
    blocked_items: dict[str, dict] = field(default_factory=dict)
    partial_items: dict[str, dict] = field(default_factory=dict)
    item_findings: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """子任务合同的返回结构：只回传精简结果，不回传全部历史。"""

    agent: str
    status: str
    summary: str
    bullets: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRecord:
    """一次任务的完整状态快照。"""

    task_id: str
    role: Role
    state: TaskState
    profile: CaseProfile | None = None
    assessment: FeasibilityAssessment | None = None
    findings: list[CriticFinding] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    agent_results: list[AgentResult] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
