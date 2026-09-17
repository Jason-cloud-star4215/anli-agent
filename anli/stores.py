"""数据层：法条库、类案库与引用核验。

对应 BACKEND_STRUCTURE.md 第 5.4 节。
业务规则：没有权威依据的内容不得进入交付物。
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path

from .models import CaseRecord, Citation, LegalProvision


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


CASE_FIELDS = {item.name for item in fields(CaseRecord)}


def case_from_dict(item: dict) -> CaseRecord:
    """容忍多出来的字段（例如抓取工具写的 title），避免旧缓存加载失败。"""

    return CaseRecord(**{key: value for key, value in item.items() if key in CASE_FIELDS})


class LawStore:
    """结构化法条库，按条号精确检索。"""

    def __init__(self, provisions: list[LegalProvision] | None = None) -> None:
        self.provisions = provisions or []

    @classmethod
    def load(cls, path: str | Path) -> "LawStore":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([LegalProvision(**item) for item in raw])

    def get(self, provision_id: str) -> LegalProvision | None:
        for item in self.provisions:
            if item.provision_id == provision_id:
                return item
        return None

    def search(
        self,
        keywords: list[str],
        limit: int = 5,
        include_unverified: bool = False,
        domain: str | None = "civil",
    ) -> list[LegalProvision]:
        """按关键词检索。domain 默认只搜民事条文，传 None 表示不限域。"""

        hits: list[tuple[float, LegalProvision]] = []
        for item in self.provisions:
            if not include_unverified and not item.verified:
                continue
            if domain is not None and item.domain != domain:
                continue
            score = _relevance(item, keywords)
            if score > 0:
                hits.append((score, item))
        hits.sort(key=lambda pair: -pair[0])
        return [item for _, item in hits[:limit]]


def _relevance(provision: LegalProvision, keywords: list[str]) -> float:
    """关键词 + 主题标签 + 二元切片的综合打分。

    纯子串匹配对「交通事故人身损害赔偿」这类长词很不友好，
    因此叠加主题标签命中与二元切片命中，避免明明相关的法条检索不到。
    """

    haystack = f"{provision.law_name}{provision.article}{provision.text}"
    topics = "".join(provision.topics)
    score = 0.0
    for keyword in keywords:
        if not keyword:
            continue
        if keyword in topics:
            score += 3.0
        elif any(topic and topic in keyword for topic in provision.topics):
            score += 2.0
        if keyword in haystack:
            score += 2.0
        grams = [keyword[i : i + 2] for i in range(max(1, len(keyword) - 1))]
        if grams:
            score += sum(1 for gram in grams if gram in haystack) / len(grams)
    return round(score, 3)


class CaseStore:
    """类案库：演示样本与真实官方案例分开放，绝不混用。

    - `cases`：内置演示样本（synthetic=True），用于演示金额区间计算，全程标注；
    - `official`：从最高人民法院官网抓来的指导性案例（真实来源、带案号与抓取时间），
      只做裁判要点参照，不参与金额区间。
    """

    def __init__(
        self,
        cases: list[CaseRecord] | None = None,
        official: list[CaseRecord] | None = None,
    ) -> None:
        self.cases = cases or []
        self.official = official or []

    @classmethod
    def load(
        cls,
        path: str | Path,
        official_path: str | Path | None = None,
        empirical_path: str | Path | None = None,
    ) -> "CaseStore":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        official: list[CaseRecord] = []
        if official_path is not None and Path(official_path).exists():
            try:
                official_raw = json.loads(
                    Path(official_path).read_text(encoding="utf-8")
                )
                official = [case_from_dict(item) for item in official_raw]
            except (OSError, ValueError, TypeError):
                official = []
        cases = [case_from_dict(item) for item in raw]
        # 实证层：人工对照判决原文核验过的真实案例（带金额），优先级最高；
        # 只有 verification="human_verified" 的记录才允许进入
        if empirical_path is not None and Path(empirical_path).exists():
            try:
                from .real_cases import RealCaseStore

                empirical = RealCaseStore.load(empirical_path)
                for record in empirical.verified():
                    cases.append(record.to_case_record())
            except (OSError, ValueError):
                pass
        return cls(cases, official)

    def search_official(
        self,
        keywords: list[str],
        case_type: str = "",
        limit: int = 3,
        min_score: float = 4.0,
    ) -> list[CaseRecord]:
        """按关键词在真实官方案例里找参照。命中不到就返回空，不编造。

        min_score 是准入线：同案由（+3）或至少两个关键词原样命中（各 +2）才算数，
        避免「惩罚性赔偿」这种通用词把知识产权案例也拉进消费纠纷的参照里。
        """

        scored: list[tuple[float, CaseRecord]] = []
        for case in self.official:
            # 案由不符一律不引用：关键词打分（如「机动车」）会把反垄断案的
            # 「机动车检测公司」拉进交通事故参照，宁可少参照也不能张冠李戴
            if case_type and case.case_type != case_type:
                continue
            haystack = (
                f"{case.dispute}{case.holding}{case.case_type}"
                f"{''.join(case.keywords)}{case.case_no}"
            )
            score = 0.0
            for keyword in keywords:
                text = (keyword or "").strip()
                if not text:
                    continue
                if text in haystack:
                    score += 2.0
                grams = [text[i : i + 2] for i in range(max(1, len(text) - 1))]
                if grams:
                    score += sum(1 for gram in grams if gram in haystack) * 0.4
            if case_type and case.case_type == case_type:
                score += 3.0
            if score >= min_score:
                scored.append((score, case))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].year))
        return [case for _, case in scored[:limit]]

    def search(
        self,
        case_type: str,
        region: str = "",
        year_from: int = 0,
        limit: int = 10,
    ) -> list[CaseRecord]:
        hits = [
            case
            for case in self.cases
            if case.case_type == case_type and case.year >= year_from
        ]
        if region:
            local = [case for case in hits if case.region == region]
            hits = local or hits
        return hits[:limit]

    def search_similar(
        self,
        case_type: str,
        evidence: list[str] | None = None,
        region: str = "",
        limit: int = 8,
    ) -> list[CaseRecord]:
        """按证据构成相似度挑选类案。

        同样的损失，证据齐不齐会直接影响判决金额，
        因此优先返回证据结构最接近的案例，而不是简单取同一案由。
        """

        candidates = [case for case in self.cases if case.case_type == case_type]
        if not candidates:
            return []
        user_evidence = set(evidence or [])

        def similarity(case: CaseRecord) -> float:
            case_evidence = set(case.evidence_points)
            if not user_evidence or not case_evidence:
                return 0.0
            overlap = len(user_evidence & case_evidence)
            union = len(user_evidence | case_evidence)
            return overlap / union if union else 0.0

        scored = sorted(
            candidates,
            key=lambda case: (similarity(case), case.region == region, case.year),
            reverse=True,
        )
        # 实证案例（人工核验、非演示）永远排在演示样本前面；
        # 实证不足时才由演示样本补位，保证区间优先反映真实判例
        empirical = [case for case in scored if not case.synthetic]
        demo = [case for case in scored if case.synthetic]
        return (empirical + demo)[:limit]


class CitationVerifier:
    """逐条核对引用是否真实存在，核验不通过即剔除。"""

    def __init__(self, law_store: LawStore, case_store: CaseStore) -> None:
        self.law_store = law_store
        self.case_store = case_store

    def verify(self, citation: Citation) -> Citation:
        if citation.source_type == "law":
            provision = self.law_store.get(citation.source_id)
            if provision is None:
                return self._reject(citation, "法条不存在于内置法条库")
            if not provision.verified:
                citation.verified = False
                citation.grade = "unverified"
                citation.note = "该条文尚未核验，不得进入交付物"
                return citation
            if _normalize(citation.quote) not in _normalize(provision.text):
                return self._reject(citation, "引文与法条原文不一致")
            citation.verified = True
            citation.grade = "authoritative"
            citation.note = f"来源：{provision.source_url or '内置法条库'}"
            return citation

        if citation.source_type == "case":
            case = next(
                (item for item in self.case_store.cases if item.case_id == citation.source_id),
                None,
            )
            if case is None:
                return self._reject(citation, "案号不存在于类案库")
            if _normalize(citation.quote) not in _normalize(case.holding):
                return self._reject(citation, "引文与裁判要旨不一致")
            citation.verified = True
            if case.synthetic:
                citation.grade = "synthetic"
                citation.note = "演示数据，可跑通流程，但不得作为最终法律依据"
            else:
                citation.grade = "authoritative"
                citation.note = f"来源：{case.source_url or case.court}"
            return citation

        return self._reject(citation, f"未知的来源类型：{citation.source_type}")

    @staticmethod
    def _reject(citation: Citation, reason: str) -> Citation:
        citation.verified = False
        citation.grade = "unverified"
        citation.note = reason
        return citation
