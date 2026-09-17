"""实证类案库：人工核验的真实判决结构化记录。

与 cases.seed.json（虚构演示数据）严格分离，三条铁律：

1. 每条记录必须来自公开渠道（人民法院案例库、裁判文书网、法院公开文书等），
   并保留可点开的来源链接；不允许凭印象或模型记忆填写任何字段；
2. verification="human_verified" 表示有人对照判决书原文逐字段核对过，
   只有这一状态的记录才会进入分析流程并标注 🟢 实证；
   "imported_pending" 表示仅初步录入，尚未对照原文核验，不进入流程；
3. 库为空或样本不足时，系统必须诚实降级（标注演示数据或无样本），
   绝不允许拿虚构数据冒充实证。

录入与核验流程见 tools/cases_cli.py 与《明日待办清单.md》。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import CaseRecord

COURT_LEVELS = {"基层人民法院", "中级人民法院", "高级人民法院", "最高人民法院"}
VERIFICATION_LEVELS = {"imported_pending", "human_verified"}
# 实证样本进入统计与流程的最小样本量；低于该值时界面必须标注样本不足
MIN_REAL_SAMPLE = 3
# 责任比例匹配窗口：先取比例接近的样本，不足 MIN_REAL_SAMPLE 再放宽
RATIO_WINDOW = 0.2


@dataclass
class RealCase:
    """一条经人工核验的真实判决记录。

    字段即录入规范：每个字段都要求能对照判决书原文核对。
    """

    case_id: str                 # 案号，如（2024）浙0108民初1234号
    court: str                   # 审理法院
    year: int                    # 判决年份
    region: str                  # 地域，如 杭州
    case_type: str               # 案由，须与系统案由一致（如 交通事故人身损害赔偿）
    injury_level: str            # 伤残等级，如 十级伤残；无伤残填 无伤残
    total_amount: float          # 判赔总额（元）
    responsibility_ratio: float  # 被告承担责任比例，0–1
    court_level: str             # 法院层级（COURT_LEVELS 之一）
    disability_auth: bool        # 是否有伤残鉴定意见
    lost_wage_method: str        # 误工费认定方式（按凭证认定 / 按行业标准计算 / 未支持 等）
    key_evidence_tags: list[str] = field(default_factory=list)  # 关键证据清单
    judge_reasoning_summary: str = ""                            # 裁判要旨摘要（原文改写须可对照）
    source_url: str = ""         # 来源链接，必须可点开
    verification: str = "imported_pending"  # imported_pending | human_verified

    def problems(self) -> list[str]:
        """逐字段校验，返回问题列表；空列表表示该记录可用于人工复核。"""

        issues: list[str] = []
        for name in ("case_id", "court", "region", "case_type", "injury_level", "lost_wage_method"):
            if not str(getattr(self, name) or "").strip():
                issues.append(f"{name} 不能为空")
        if not isinstance(self.year, int) or not 1990 <= self.year <= 2100:
            issues.append("year 必须是 1990–2100 的整数")
        if not isinstance(self.total_amount, (int, float)) or self.total_amount <= 0:
            issues.append("total_amount 必须是大于 0 的数值")
        if not isinstance(self.responsibility_ratio, (int, float)) or not 0 < self.responsibility_ratio <= 1:
            issues.append("responsibility_ratio 必须在 (0, 1] 区间")
        if self.court_level not in COURT_LEVELS:
            issues.append(f"court_level 必须是 {sorted(COURT_LEVELS)} 之一")
        if not isinstance(self.disability_auth, bool):
            issues.append("disability_auth 必须是布尔值")
        if not self.key_evidence_tags or not all(isinstance(tag, str) and tag.strip() for tag in self.key_evidence_tags):
            issues.append("key_evidence_tags 必须是非空字符串列表")
        if len(self.judge_reasoning_summary.strip()) < 10:
            issues.append("judge_reasoning_summary 至少 10 个字，须能对照判决原文")
        if not self.source_url.lower().startswith("http"):
            issues.append("source_url 必须是以 http 开头的公开来源链接")
        if self.verification not in VERIFICATION_LEVELS:
            issues.append(f"verification 必须是 {sorted(VERIFICATION_LEVELS)} 之一")
        return issues

    def to_case_record(self) -> CaseRecord:
        """转成管线通用的 CaseRecord；synthetic=False，走实证引用通道。"""

        return CaseRecord(
            case_id=self.case_id,
            court=self.court,
            year=self.year,
            region=self.region,
            case_type=self.case_type,
            dispute=f"{self.injury_level}｜被告责任{self.responsibility_ratio:.0%}",
            holding=self.judge_reasoning_summary,
            amounts={"合计": float(self.total_amount)},
            evidence_points=list(self.key_evidence_tags),
            source_url=self.source_url,
            synthetic=False,
            source_name="实证类案（人工核验）",
        )


def validate_record(data: dict) -> list[str]:
    """对一条原始 JSON 记录做校验，返回问题列表。"""

    try:
        record = RealCase(**data)
    except TypeError as exc:
        return [f"字段结构不符合规范：{exc}"]
    return record.problems()


class RealCaseStore:
    """实证类案库：只保留通过校验的记录，并集中暴露校验问题。"""

    def __init__(self, records: list[RealCase] | None = None, problems: list[str] | None = None) -> None:
        self.records = records or []
        self.problems = problems or []

    @classmethod
    def load(cls, path: str | Path) -> "RealCaseStore":
        """从 JSON 文件加载；文件缺失或为空列表都是合法状态（库待补录）。"""

        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        records: list[RealCase] = []
        problems: list[str] = []
        for index, item in enumerate(raw, start=1):
            issues = validate_record(item)
            if issues:
                problems.extend(f"第 {index} 条（{item.get('case_id', '?')}）：{issue}" for issue in issues)
                continue
            records.append(RealCase(**item))
        return cls(records, problems)

    def verified(self) -> list[RealCase]:
        """只有人工核验过的记录才有资格进入分析流程。"""

        return [record for record in self.records if record.verification == "human_verified"]

    def matching_case_records(
        self,
        cause: str,
        region: str = "",
        responsibility_ratio: float = 1.0,
    ) -> list[CaseRecord]:
        """挑出与本案同案由、责任比例接近的实证记录，转成 CaseRecord。

        先按责任比例 ±0.2 过滤；候选不足 MIN_REAL_SAMPLE 件时放宽到全部同案由样本，
        避免「为凑比例把样本砍到 0」导致明明有实证却报无样本。
        """

        candidates = [record for record in self.verified() if record.case_type == cause]
        if not candidates:
            return []
        close = [
            record
            for record in candidates
            if abs(record.responsibility_ratio - responsibility_ratio) <= RATIO_WINDOW
        ]
        chosen = close if len(close) >= MIN_REAL_SAMPLE else candidates
        if region:
            local = [record for record in chosen if record.region == region]
            chosen = local or chosen
        return [record.to_case_record() for record in chosen]

    def stats(
        self,
        cause: str,
        responsibility_ratio: float = 1.0,
    ) -> dict | None:
        """实证样本的金额统计：n、下四分位、中位数、上四分位。

        没有样本时返回 None——调用方必须如实标注「无实证样本」，
        不允许回退到演示数据冒充实证统计。
        """

        matched = self.matching_case_records(cause, responsibility_ratio=responsibility_ratio)
        amounts = [float(case.total_amount) for case in matched if case.total_amount > 0]
        if not amounts:
            return None
        ordered = sorted(amounts)
        if len(ordered) >= 4:
            quartiles = statistics.quantiles(ordered, n=4, method="inclusive")
            p25, median, p75 = quartiles[0], quartiles[1], quartiles[2]
        else:
            p25, median, p75 = ordered[0], statistics.median(ordered), ordered[-1]
        return {
            "n": len(ordered),
            "p25": round(p25, 2),
            "median": round(median, 2),
            "p75": round(p75, 2),
            "source": "real_cases",
        }

    def template(self) -> dict:
        """返回一条符合字段规范的空白模板（供 tools/cases_cli.py 使用）。"""

        return {
            "case_id": "（2024）浙0108民初XXXX号",
            "court": "杭州市XX区人民法院",
            "year": 2024,
            "region": "杭州",
            "case_type": "交通事故人身损害赔偿",
            "injury_level": "十级伤残",
            "total_amount": 0,
            "responsibility_ratio": 0.7,
            "court_level": "基层人民法院",
            "disability_auth": True,
            "lost_wage_method": "按凭证认定",
            "key_evidence_tags": ["事故认定书", "医疗费票据", "误工证明"],
            "judge_reasoning_summary": "对照判决书原文摘写裁判要旨，至少 10 个字。",
            "source_url": "https://…（人民法院案例库 / 裁判文书网 / 法院公开文书链接）",
            "verification": "imported_pending",
        }

    def dump(self) -> list[dict]:
        return [asdict(record) for record in self.records]
