"""编排层：Lead Agent。

负责拆阶段、分派角色、控制预算、汇总结果，不亲自完成检索细节。
对应 BACKEND_STRUCTURE.md 第 2 节与第 5 节。
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date

from .agents import (
    ACTION_LABEL,
    AGENT_FACTORY,
    AgentContext,
    AdvocacyAgent,
)
from .events import Event, EventBus
from .evidence_chain import (
    build_evidence_requests,
    evidence_sufficiency,
    render_requests,
)
from .item_assessment import STATUS_LABEL
from .feasibility import build_assessment
from .cross_exam import build_challenges, render_challenges
from .judgment import render_judgment
from .standards import StandardsStore
from .models import (
    Artifact,
    CaseProfile,
    CriticFinding,
    Role,
    Severity,
    TaskRecord,
    TaskState,
)
from .state import Budget, StateStore, transition
from .stores import CaseStore, CitationVerifier, LawStore
from .scope import (
    HUMAN_CHANNELS,
    detect_out_of_scope,
    human_review_artifact,
    human_review_reasons,
)
from .strategy import approx_range, build_strategies, format_range, render_summary
from .tools import ToolCall, ToolRouter
from .tools_builtin import build_default_registry

# 转人工时先给「事实与数字」，行动方案与文书草稿要等用户确认风险之后再给
SAFE_ARTIFACT_KINDS = {
    "summary",
    "feasibility",
    "evidence",
    "evidence_request",
    "research",
    "calculation",
}


class Orchestrator:
    def __init__(
        self,
        law_store: LawStore,
        case_store: CaseStore,
        state_store: StateStore | None = None,
        bus: EventBus | None = None,
        llm=None,
        law_checker=None,
        llm_configured: bool | None = None,
        standards_store: StandardsStore | None = None,
    ) -> None:
        self.law_store = law_store
        self.case_store = case_store
        self.standards = standards_store or StandardsStore()
        self.registry = build_default_registry(law_store, case_store, law_checker)
        self.router = ToolRouter(self.registry)
        self.verifier = CitationVerifier(law_store, case_store)
        self.state_store = state_store or StateStore()
        self.bus = bus or EventBus()
        self.budget = Budget()
        self.llm = llm
        # 「配了密钥但这次选择不调用」和「压根没配密钥」是两回事，不能都算掉线
        self.llm_configured = (
            (llm is not None) if llm_configured is None else bool(llm_configured)
        )

    # ------------------------------------------------------------------ 对外入口
    def run(
        self,
        task_id: str,
        role: Role,
        profile: CaseProfile,
        expectation: float,
        approved: bool = False,
    ) -> TaskRecord:
        record = TaskRecord(task_id=task_id, role=role, state=TaskState.RECEIVED)
        record.extra["approved"] = approved
        # 同一个实例可以连续跑多个任务：先清零已用轮次，但保留上限设置
        self.budget.reset()
        self.bus.emit("task_created", task_id, {"role": role.value})

        try:
            record.profile = profile
            if not profile.summary.strip():
                self._stage(record, TaskState.CLARIFYING)
                self._stage(record, TaskState.NEEDS_INFO)
                self._snapshot_llm_usage(record)
                self._emit_llm_state_on_stop(task_id, "没有收到案情描述")
                return record

            self._stage(record, TaskState.CLARIFYING)
            self._stage(record, TaskState.CLASSIFIED)

            # 红线：刑事、行政案件不出分析结论，直接停下并给求助渠道（确认也不能继续）
            scope = detect_out_of_scope(profile.description or profile.summary)
            if scope is not None:
                scope.legal_refs = self._out_of_scope_refs(scope)
                body = scope.render()
                self._snapshot_llm_usage(record)
                self._emit_llm_state_on_stop(task_id, "判定超出覆盖范围")
                record.extra["out_of_scope"] = {
                    "kind": scope.kind,
                    "matched": scope.matched,
                    "channels": scope.channels,
                    "legal_refs": scope.legal_refs,
                }
                record.extra["summary"] = body
                record.artifacts = [
                    Artifact(kind="out_of_scope", title=scope.title, body=body)
                ]
                self.bus.emit(
                    "artifact_ready", task_id, {"kind": "out_of_scope"}
                )
                self._stage(record, TaskState.NEEDS_HUMAN)
                self.bus.emit(
                    "task_completed", task_id, {"artifacts": len(record.artifacts)}
                )
                return record

            # 认不出案由或算不出金额时，不硬跑，直接要求补充信息
            if profile.cause == "一般民事纠纷" and not profile.claim_items:
                message = (
                    "还无法判断你的纠纷类型和损失金额。请补充：\n"
                    "1. 事情经过（时间、对方是谁、发生了什么）；\n"
                    "2. 你的损失金额和你希望的结果；\n"
                    "3. 手上已有哪些材料（合同、票据、聊天记录、认定书等）。"
                )
                record.extra["summary"] = message
                record.artifacts = [
                    Artifact(kind="need_info", title="需要补充信息", body=message)
                ]
                self._stage(record, TaskState.NEEDS_INFO)
                self._snapshot_llm_usage(record)
                self._emit_llm_state_on_stop(task_id, "案情信息不足")
                self.bus.emit(
                    "task_completed", task_id, {"artifacts": len(record.artifacts)}
                )
                return record

            # 只有「一项金额都没写出来」才回去要金额（例如模板只填了下划线没填数字）。
            # 金额写了但缺证据的，属于证据链问题，交给后面的补证清单，不能退回要金额。
            quantified = [
                item for item in (profile.item_findings or []) if item.claimed > 0
            ]
            if not quantified:
                pending = profile.extra.get("unquantified_items") or []
                lines = ["还没有识别到任何具体的损失金额，现在无法计算，先补充这几项："]
                if pending:
                    lines += [f"· {name}：填上具体金额" for name in pending]
                else:
                    lines.append("· 各项损失的具体金额（例如：医疗费 3200 元）")
                lines += [
                    "",
                    "金额写清楚之后，系统才能逐项判断能不能支持、按什么标准计算。",
                ]
                message = "\n".join(lines)
                record.extra["summary"] = message
                record.artifacts = [
                    Artifact(kind="need_info", title="需要补充信息", body=message)
                ]
                self._stage(record, TaskState.NEEDS_INFO)
                self._snapshot_llm_usage(record)
                self._emit_llm_state_on_stop(task_id, "还没有识别到具体金额")
                self.bus.emit(
                    "task_completed", task_id, {"artifacts": len(record.artifacts)}
                )
                return record

            self._stage(record, TaskState.RESEARCHING)
            research = self._run_research(record)
            # 引用核验只做一次：同一批核验结果既供挑刺审查判断，也直接进交付物
            record.citations = self._verify_all(research.citations, task_id)
            # 官方库核对结果留档：红队审查与交付物都要用
            law_sources = research.payload.get("law_sources") or []
            record.extra["law_sources"] = [asdict(item) for item in law_sources]
            official_cases = research.payload.get("official_cases") or []
            record.extra["official_cases"] = [asdict(item) for item in official_cases]

            legal_cap = float(sum(profile.claim_items.values()))
            blocked = dict(profile.blocked_items)
            context = AgentContext(
                task_id=task_id,
                profile=profile,
                legal_cap=legal_cap,
                expectation=expectation,
            )
            context.extra["blocked"] = blocked

            self._stage(record, TaskState.DEBATING)
            debate = self._run_debate(context)
            record.agent_results.extend(debate)

            self._stage(record, TaskState.CRITIQUING)
            cases = list(research.payload.get("cases", [])) or self.case_store.search_similar(
                case_type=profile.cause,
                evidence=profile.available_evidence,
                region=profile.region,
            )
            # 实证充足（同案由人工核验 ≥3 件）时，区间只用实证案例，演示样本不掺入；
            # 不足时保留演示样本并强制标注 🟡，绝不冒充真实判例
            empirical_cases = [c for c in cases if not c.synthetic and c.total_amount > 0]
            if len(empirical_cases) >= 3:
                cases = empirical_cases
            findings, redteam = self._run_critique(
                context, record, cases, legal_cap
            )
            for extra in redteam.payload.get("extra_findings") or []:
                if all(extra.detail != item.detail for item in findings):
                    findings.append(extra)
            record.agent_results.append(redteam)

            # 多轮对抗：只要还有未解决的中/高严重度问题，就再走一轮
            # 「修订方案 → 对方针对修订再反驳 → 再次复核」，直到没有未解决问题或到轮次上限
            revisions: list[dict] = []
            round_no = 1
            while round_no < self.budget.max_critic_rounds:
                open_items = self._open_findings(findings)
                if not open_items:
                    break
                round_no += 1
                self.bus.emit(
                    "round_started",
                    task_id,
                    {
                        "round": round_no,
                        "total": self.budget.max_critic_rounds,
                        "open_findings": len(open_items),
                    },
                )
                context.extra["open_findings"] = open_items
                revision = AGENT_FACTORY["revision"](
                    stance=self._revision_stance(record.role),
                    router=self.router,
                    bus=self.bus,
                    budget=self.budget,
                    llm=self.llm,
                ).run(context)
                record.agent_results.append(revision)
                changes = revision.payload.get("changes") or []
                self._apply_changes(findings, changes)
                revisions.append(
                    {
                        "round": round_no,
                        "summary": revision.summary,
                        "changes": changes,
                    }
                )
                context.extra["revision_summary"] = revision.summary

                rebuttal = self._run_rebuttal(context, record)
                record.agent_results.append(rebuttal)

                more, redteam_round = self._run_critique(
                    context, record, cases, legal_cap, round_no=round_no
                )
                findings = self._merge_findings(findings, more)
                record.agent_results.append(redteam_round)

            record.findings = findings
            record.extra["revisions"] = revisions

            self._stage(record, TaskState.EVALUATING)
            judge = AGENT_FACTORY["judge"](
                router=self.router, bus=self.bus, budget=self.budget, llm=self.llm
            ).run(context)
            record.agent_results.append(judge)

            record.assessment = build_assessment(
                expectation=expectation,
                legal_cap=legal_cap,
                cases=cases,
                responsibility_ratio=profile.responsibility_ratio,
                region=profile.region,
                sufficiency=float((profile.extra or {}).get("sufficiency", 1.0)),
                blocked_amount=sum(
                    float(info.get("amount") or 0) for info in blocked.values()
                ),
                claimed_amount=sum(
                    float(getattr(item, "claimed", 0) or 0)
                    for item in (profile.item_findings or [])
                ),
                expectation_label={
                    Role.DEFENDANT: "对方主张金额",
                    Role.JUDGE: "标的金额",
                    Role.CLAIMANT: "期望金额",
                }[role],
                gap_reasons=[
                    f"缺{'、'.join(info.get('missing') or [])}，{item} {float(info.get('amount') or 0):.0f} 元未计入"
                    for item, info in blocked.items()
                ],
                uplift_paths=[
                    f"补齐{'、'.join(info.get('missing') or [])}可提高约 {float(info.get('amount') or 0):.0f} 元"
                    for info in blocked.values()
                ] + list(profile.evidence_notes),
            )
            limitation = self.router.call(
                Role.REDTEAM,
                ToolCall(tool="calc_limitation", arguments={"case_type": profile.cause}),
            )
            limitation_days = (
                date.fromisoformat(limitation["deadline"]) - date.today()
            ).days
            strategies = build_strategies(
                profile,
                record.assessment,
                blocked,
                limitation_days=limitation_days,
                role=record.role,
            )
            requests = build_evidence_requests(profile, role)
            summary = render_summary(
                profile, record.assessment, strategies, role=record.role
            )
            if requests:
                summary += (
                    f"\n\n提醒：以上是初步结论。你的证据链还缺 {len(requests)} 项，"
                    "补齐后金额判断会更准，详见下一节。"
                )
            record.extra["strategies"] = strategies
            record.extra["summary"] = summary
            record.extra["evidence_sufficiency"] = evidence_sufficiency(profile)
            if requests:
                record.extra["evidence_request"] = [asdict(item) for item in requests]
            record.extra["llm_calls"] = getattr(self.llm, "calls", 0) if self.llm else 0
            record.extra["llm_failures"] = (
                getattr(self.llm, "failures", 0) if self.llm else 0
            )
            record.extra["intake_source"] = profile.intake_source

            record.artifacts = self._build_artifacts(
                record,
                research.bullets,
                debate,
                judge,
                findings,
                expectation,
                strategies,
                requests,
            )

            # 转人工闸门：金额重大、时效临近、涉人身安全时，
            # 先只交付事实与数字，行动方案与文书草稿等用户确认风险后再给
            risk_reasons = human_review_reasons(
                profile.description or profile.summary,
                expectation,
                limitation_days,
            )
            if risk_reasons and not approved:
                record.extra["human_review"] = risk_reasons
                record.extra["human_channels"] = HUMAN_CHANNELS
                kept = [
                    item
                    for item in record.artifacts
                    if item.kind in SAFE_ARTIFACT_KINDS
                ]
                kept.insert(
                    1,
                    Artifact(
                        kind="human_review",
                        title="需要人工介入（已暂停交付）",
                        body=human_review_artifact(risk_reasons),
                    ),
                )
                record.artifacts = kept
                record.extra["summary"] = (
                    str(record.extra.get("summary", ""))
                    + "\n\n提示：本次分析触发了转人工条件，行动路径与文书草稿"
                    "需要你确认风险之后才会给出。"
                )
            for artifact in record.artifacts:
                self.bus.emit("artifact_ready", task_id, {"kind": artifact.kind})

            if risk_reasons and not approved:
                self.bus.emit(
                    "approval_required", task_id, {"reasons": risk_reasons}
                )
                self._stage(record, TaskState.NEEDS_HUMAN)
                self.bus.emit(
                    "task_completed", task_id, {"artifacts": len(record.artifacts)}
                )
                return record

            if requests:
                self.bus.emit(
                    "evidence_requested", task_id, {"count": len(requests)}
                )
                self._stage(record, TaskState.NEEDS_EVIDENCE)
            else:
                self._stage(record, TaskState.DELIVERED)
            self.bus.emit("task_completed", task_id, {"artifacts": len(record.artifacts)})
        except Exception as exc:  # noqa: BLE001 - 编排层兜底，保留检查点
            record.state = TaskState.FAILED
            record.extra["error"] = str(exc)
            self.bus.emit("task_failed", task_id, {"error": str(exc)})
            self._checkpoint(record)
        return record

    # ------------------------------------------------------------------ 各阶段
    def _run_research(self, record: TaskRecord):
        agent = AGENT_FACTORY["researcher"](
            router=self.router, bus=self.bus, budget=self.budget, llm=self.llm
        )
        context = AgentContext(task_id=record.task_id, profile=record.profile)
        # 法条与类案两条检索的并发在 ResearcherAgent 内部完成，
        # 这里不再重复发起一次 case_search（此前那次调用的结果被丢弃，纯属浪费）
        result = agent.run(context)
        record.agent_results.append(result)
        return result

    def _run_debate(self, context: AgentContext) -> list:
        claimant = AdvocacyAgent(
            Role.CLAIMANT,
            router=self.router,
            bus=self.bus,
            budget=self.budget,
            llm=self.llm,
        ).run(context)
        defendant = AdvocacyAgent(
            Role.DEFENDANT,
            router=self.router,
            bus=self.bus,
            budget=self.budget,
            llm=self.llm,
        ).run(context)
        return [claimant, defendant]

    def _run_rebuttal(self, context: AgentContext, record: TaskRecord):
        """第二轮的对方反驳：针对刚才的修订说明再打一轮。"""

        stance = (
            Role.CLAIMANT
            if self._revision_stance(record.role) == Role.DEFENDANT
            else Role.DEFENDANT
        )
        return AdvocacyAgent(
            stance,
            router=self.router,
            bus=self.bus,
            budget=self.budget,
            llm=self.llm,
        ).run(context)

    @staticmethod
    def _revision_stance(role: Role) -> Role:
        """修订的是用户这一侧的方案；法官视角下按主张方口径整理。"""

        return Role.DEFENDANT if role == Role.DEFENDANT else Role.CLAIMANT

    def _out_of_scope_refs(self, scope) -> list[str]:
        """在刑事 / 行政域里找相关条文，只作「你去核对」的指引，不做分析。"""

        domain = "criminal" if scope.kind == "刑事" else "administrative"
        hits = self.law_store.search(
            list(scope.matched), limit=3, domain=domain
        )
        refs: list[str] = []
        for item in hits:
            snippet = re.sub(
                r"^第[一二三四五六七八九十百零]+条(?:之[一二三四五六七八九十]+)?", "", item.text
            )
            refs.append(
                f"{item.label}（{snippet[:44]}…，来源：{item.source_url or '内置法条库'}）"
            )
        return refs

    @staticmethod
    def _open_findings(findings: list[CriticFinding]) -> list[CriticFinding]:
        return [
            item
            for item in findings
            if not item.resolved and item.severity in {Severity.HIGH, Severity.MEDIUM}
        ]

    @staticmethod
    def _merge_findings(
        existing: list[CriticFinding], incoming: list[CriticFinding]
    ) -> list[CriticFinding]:
        """按描述去重：保留上一轮已标记的处理结果，只追加新问题。"""

        seen = {item.detail for item in existing}
        return list(existing) + [
            item for item in incoming if item.detail not in seen
        ]

    @staticmethod
    def _apply_changes(
        findings: list[CriticFinding], changes: list[dict]
    ) -> None:
        """把修订说明里的「已修订」落到对应的问题上，避免假迭代。"""

        fixed_categories = {
            str(item.get("category") or "")
            for item in changes
            if str(item.get("action")) == "fixed"
        }
        for finding in findings:
            if finding.category in fixed_categories:
                finding.resolved = True

    def _run_critique(self, context, record, cases, legal_cap, round_no: int = 1):
        self.budget.spend_critic_round()
        findings = self._detect_findings(context, record, cases, legal_cap)
        context.extra["findings"] = findings
        redteam = AGENT_FACTORY["redteam"](
            router=self.router, bus=self.bus, budget=self.budget, llm=self.llm
        ).run(context)
        for finding in findings:
            self.bus.emit(
                "critic_finding",
                record.task_id,
                {
                    "category": finding.category,
                    "severity": finding.severity.value,
                    "detail": finding.detail,
                },
            )
        return findings, redteam

    def _detect_findings(self, context, record, cases, legal_cap):
        profile = record.profile
        findings: list[CriticFinding] = []

        # 引用核验结果已在上游算过一次，这里直接复用，不再重复回源
        unverified = [c for c in record.citations if not c.verified]
        if unverified:
            findings.append(
                CriticFinding(
                    category="引用核验",
                    severity=Severity.HIGH,
                    detail=f"{len(unverified)} 条引用未通过核验，必须从交付物中剔除",
                    basis="业务规则：每条结论必须有可追溯依据",
                )
            )

        if len(cases) < 3:
            findings.append(
                CriticFinding(
                    category="样本不足",
                    severity=Severity.HIGH,
                    detail=f"同类案例仅 {len(cases)} 件，实践区间参考价值有限",
                    basis="业务规则：样本不足必须显式标注",
                )
            )

        if profile.missing_evidence:
            findings.append(
                CriticFinding(
                    category="证据缺口",
                    severity=Severity.MEDIUM,
                    detail="按案由要求，仍缺少：" + "、".join(profile.missing_evidence),
                    basis="赔偿项目需要对应证据支撑",
                )
            )

        for item, info in (record.profile.blocked_items or {}).items():
            amount = float(info.get("amount") or 0)
            missing = "、".join(info.get("missing") or [])
            findings.append(
                CriticFinding(
                    category="金额未计入",
                    severity=Severity.MEDIUM if amount >= 1000 else Severity.LOW,
                    detail=f"{item} {amount:.0f} 元因缺少 {missing} 未计入可支持金额",
                    basis="缺证据的赔偿项目不能作为主张上限",
                )
            )

        if context.expectation > legal_cap:
            capped = legal_cap * profile.responsibility_ratio
            findings.append(
                CriticFinding(
                    category="诉求超出法定上限",
                    severity=Severity.MEDIUM,
                    detail=f"期望 {context.expectation:.0f} 元高于按现有证据和责任比例计算的上限 {capped:.0f} 元",
                    basis="按现有证据可支持的赔偿项目合计，再乘以责任比例",
                )
            )

        for item in profile.extra.get("unquantified_items") or []:
            findings.append(
                CriticFinding(
                    category="金额缺失",
                    severity=Severity.MEDIUM,
                    detail=f"{item}没有给出具体金额，未计入可支持上限",
                    basis="没有金额无法计算该项能支持多少",
                )
            )

        limitation = self.router.call(
            Role.REDTEAM,
            ToolCall(tool="calc_limitation", arguments={"case_type": profile.cause}),
        )
        findings.append(
            CriticFinding(
                category="时效提示",
                severity=Severity.LOW,
                detail=f"适用{limitation['rule']}，预计截止 {limitation['deadline']}",
                basis="内置时效计算工具",
            )
        )

        # 官方库核对结果：在库里查不到的法，引用必须人工复核
        for entry in record.extra.get("law_sources") or []:
            if entry.get("error") or entry.get("found"):
                continue
            findings.append(
                CriticFinding(
                    category="法条来源存疑",
                    severity=Severity.HIGH,
                    detail=f"《{entry.get('law_name')}》未在国家法律法规数据库中检索到，引用前必须人工复核",
                    basis="国家法律法规数据库官方检索接口",
                )
            )
        return findings

    # ------------------------------------------------------------------ 工具方法
    def _verify(self, citation, task_id: str = ""):
        verified = self.verifier.verify(citation)
        self.bus.emit(
            "citation_verified",
            task_id,
            {
                "source_type": verified.source_type,
                "label": verified.label,
                "verified": verified.verified,
                "grade": verified.grade,
                "note": verified.note,
            },
        )
        return verified

    def _verify_all(self, citations, task_id: str = ""):
        return [self._verify(citation, task_id) for citation in citations]

    def _stage(self, record: TaskRecord, target: TaskState) -> None:
        record.state = transition(record.state, target)
        self.bus.emit("stage_changed", record.task_id, {"state": target.value})
        self._checkpoint(record)

    # ------------------------------------------------------------------ 模型状态
    def _snapshot_llm_usage(self, record: TaskRecord) -> None:
        """把当前累计的模型调用/失败次数写进任务（早退分支也要写，否则会被当成「没调用」）。"""

        record.extra["llm_calls"] = getattr(self.llm, "calls", 0) if self.llm else 0
        record.extra["llm_failures"] = getattr(self.llm, "failures", 0) if self.llm else 0
        record.extra["llm_configured"] = self.llm_configured

    def _emit_llm_state_on_stop(self, task_id: str, reason: str) -> None:
        """流程在「调模型之前」结束时，立刻把真实状态推给前端。

        注意：案情理解那一步可能已经调过模型（calls >= 1），
        所以不能说「未调用」，要说清「只用于理解案情、后续未再调用」。
        """

        self.bus.emit("llm_state", task_id, self._stopped_llm_state(reason))

    def _stopped_llm_state(self, reason: str) -> dict:
        calls = getattr(self.llm, "calls", 0) if self.llm else 0
        failures = getattr(self.llm, "failures", 0) if self.llm else 0
        if not self.llm_configured:
            return {"status": "offline", "text": "未配置大模型，本次为规则版流程"}
        if failures and not calls:
            return {
                "status": "degraded",
                "text": f"大模型 {failures} 次调用全部失败，已退回规则版",
            }
        if not calls:
            return {
                "status": "skipped",
                "text": f"本次未调用大模型（{reason}）",
            }
        return {
            "status": "intake_only",
            "text": f"大模型仅用于理解案情（已调用 {calls} 次），{reason}后未再调用",
        }

    def _checkpoint(self, record: TaskRecord) -> None:
        self.state_store.save(
            record.task_id,
            record.state,
            {
                "role": record.role.value,
                "artifacts": [item.kind for item in record.artifacts],
                "findings": len(record.findings),
            },
        )

    def subscribe(self, handler) -> None:
        self.bus.subscribe(handler)

    def render_report(self, record: TaskRecord) -> str:
        lines: list[str] = []
        numerals = [
            "一", "二", "三", "四", "五", "六",
            "七", "八", "九", "十", "十一", "十二",
        ]
        for index, artifact in enumerate(record.artifacts):
            number = numerals[index] if index < len(numerals) else str(index + 1)
            lines.append(f"===== {number}、{artifact.title} =====")
            lines.append(artifact.body)
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 交付物
    def _build_artifacts(
        self,
        record,
        research_bullets,
        debate,
        judge,
        findings,
        expectation,
        strategies,
        requests=None,
    ) -> list[Artifact]:
        profile = record.profile
        assessment = record.assessment
        verified = [c for c in record.citations if c.verified]
        artifacts: list[Artifact] = []

        artifacts.append(
            Artifact(
                kind="summary",
                title="结论摘要",
                body=str(record.extra.get("summary", "")),
            )
        )

        if requests:
            artifacts.append(
                Artifact(
                    kind="evidence_request",
                    title="需要你补充的证据",
                    body=render_requests(requests, record.role),
                )
            )

        strategy_lines = []
        for index, item in enumerate(strategies, start=1):
            materials = f"｜需要：{'、'.join(item.materials)}" if item.materials else ""
            strategy_lines.append(
                f"{index}. [{item.priority}] {item.action}\n"
                f"   理由：{item.reason}\n"
                f"   预期：{item.expected}{materials}"
            )
        artifacts.append(
            Artifact(
                kind="strategy",
                title="策略建议（按优先级）",
                body="\n".join(strategy_lines) or "暂无可执行建议。",
            )
        )

        # 立场分析：同一份材料，三种身份要看的重点不同
        def bullets_of(result) -> list[str]:
            if not result or not result.bullets:
                return ["· 暂无"]
            return [f"· {line}" for line in result.bullets]

        claimant = next((r for r in debate if r.agent == Role.CLAIMANT.value), None)
        defendant = next((r for r in debate if r.agent == Role.DEFENDANT.value), None)
        if record.role == Role.DEFENDANT:
            stance_title = "我方抗辩要点与对方主张"
            stance_body = "\n".join(
                ["【我方抗辩要点】"]
                + bullets_of(defendant)
                + ["", "【对方可能的主张】"]
                + bullets_of(claimant)
                + ["", "【中立评估】"]
                + bullets_of(judge)
            )
        elif record.role == Role.JUDGE:
            stance_title = "中立评估与双方要点"
            stance_body = "\n".join(
                ["【中立评估】"]
                + bullets_of(judge)
                + ["", "【主张方要点】"]
                + bullets_of(claimant)
                + ["", "【抗辩方要点】"]
                + bullets_of(defendant)
            )
        else:
            stance_title = "我方主张与对方可能的抗辩"
            stance_body = "\n".join(
                ["【我方主张要点】"]
                + bullets_of(claimant)
                + ["", "【对方可能的抗辩】"]
                + bullets_of(defendant)
                + ["", "【中立评估】"]
                + bullets_of(judge)
            )
        artifacts.append(
            Artifact(kind="stance", title=stance_title, body=stance_body)
        )

        # 结构化质证：逐条落到「项目+证据+异议类型+金额+成立后果」，按身份给不同打法
        challenges = build_challenges(profile)
        record.extra["challenges"] = [item.to_dict() for item in challenges]
        artifacts.append(
            Artifact(
                kind="cross_exam",
                title="质证争点清单",
                body=render_challenges(challenges, record.role),
            )
        )

        artifacts.append(
            Artifact(
                kind="analysis",
                title="案件分析",
                body="\n".join(
                    [
                        f"案由：{profile.cause}",
                        f"你的原话：{profile.description or profile.summary}",
                        f"案情理解方式：{'大模型抽取' if profile.intake_source == 'llm' else '规则抽取'}",
                        f"争议焦点：{'、'.join(profile.disputed_points)}",
                        (
                            f"责任比例：{profile.responsibility_ratio:.0%}"
                            if (profile.extra or {}).get("responsibility_applies", True)
                            else "责任比例：不适用（该案由不按责任比例分摊，按 100% 计）"
                        ),
                        f"已提交材料：{'、'.join(profile.available_evidence) or '无'}",
                        "时间线：" + ("；".join(profile.timeline) or "未从描述中识别"),
                    ]
                ),
            )
        )

        if assessment is None:  # pragma: no cover - 防御性分支
            artifacts.append(
                Artifact(kind="feasibility", title="四、诉求可行性评估", body="评估未生成")
            )
        else:
            labels = {
                Role.DEFENDANT: ("对方主张", "可能被支持的金额", "你的合理赔付预期"),
                Role.JUDGE: ("本案标的", "按现有证据可支持", "合理裁判区间"),
                Role.CLAIMANT: ("你的期望", "按现有证据可支持", "最可能落点"),
            }[record.role]
            if assessment.provisional:
                feasible = "暂不下结论（待补证）"
            else:
                feasible = "可以支持" if assessment.expectation_supported else "与预期存在差距"
            region_line = (
                f"适用地区：{assessment.region}（本地区样本 {assessment.region_sample_size} 件）"
                if assessment.region
                else "适用地区：未指定"
            )
            gate_line = (
                f"结论状态：证据不充分（充分度 {assessment.sufficiency:.0%}，"
                f"约 {assessment.blocked_amount:.0f} 元因缺证据未计入），"
                "本次不给结论性的赔付数字，补齐证据后重新评估"
                if assessment.provisional
                else f"结论状态：证据充分度 {assessment.sufficiency:.0%}，可给出结论性估算"
            )
            # 实证感知：非演示样本即人工核验的实证类案，亮 🟢；纯演示时维持原警告
            real_sample = assessment.sample_size - assessment.synthetic_sample_size
            if assessment.has_synthetic:
                nature_line = (
                    "样本性质：含演示数据，仅用于演示检索与计算流程，不是真实裁判结果"
                    + (f"（其中真实来源 {real_sample} 件）" if real_sample else "")
                )
            elif real_sample:
                nature_line = (
                    f"样本性质：🟢 实证类案 {real_sample} 件（人工核验，附来源链接）"
                )
            else:
                nature_line = "样本性质：真实来源案例"
            likely_text = approx_range(assessment.likely_low, assessment.likely_high)
            if assessment.provisional:
                cap_line = (
                    f"{labels[1]}：待补证（按现有证据约 {assessment.legal_cap:.0f} 元）"
                )
                likely_line = f"{labels[2]}：暂不定（证据补齐后可参考{likely_text}）"
            else:
                cap_line = f"{labels[1]}：约 {assessment.legal_cap:.0f} 元"
                likely_line = f"{labels[2]}：{likely_text}"
            artifacts.append(
                Artifact(
                    kind="feasibility",
                    title="诉求可行性评估",
                    body="\n".join(
                        [
                            f"{labels[0]}：{assessment.expectation:.0f} 元",
                            cap_line,
                            f"同类案例区间：约 {format_range(assessment.case_low, assessment.case_high)} 元"
                            f"（中位数 {_fmt(assessment.case_median)} 元，样本 {assessment.sample_size} 件）",
                            region_line,
                            "样本构成：" + assessment.sample_note,
                            nature_line,
                            likely_line,
                            f"结论：{labels[0]}{feasible}",
                            gate_line,
                            "金额口径：除期望 / 主张 / 标的这类由你自己提出的数额外，"
                            "本报告的金额都是按现有材料估算的大致数，实际以证据和裁判认定为准。",
                            "差距原因：" + ("；".join(assessment.gap_reasons) or "无"),
                            "提升路径：" + ("；".join(assessment.uplift_paths) or "无"),
                        ]
                    ),
                )
            )

        artifacts.append(
            Artifact(
                kind="evidence",
                title="证据清单与补强建议",
                body="\n".join(
                    ["已具备："]
                    + [
                        f"  - {item}（{profile.evidence_sources.get(item, '来源待确认')}）"
                        for item in profile.available_evidence
                    ]
                    + ["缺口："]
                    + [f"  - {item}（缺失，直接影响可支持金额）" for item in profile.missing_evidence]
                    + (["说明："] + [f"  - {note}" for note in profile.evidence_notes]
                       if profile.evidence_notes else [])
                ),
            )
        )

        case_lines = [
            f"- {c.label}：{c.note}" for c in verified if c.source_type == "case"
        ] or ["- 未检索到可用类案，已标注样本不足"]
        law_lines = [f"- {c.label}：{c.note}" for c in verified if c.source_type == "law"]

        law_source_lines: list[str] = []
        for entry in record.extra.get("law_sources") or []:
            name = entry.get("law_name") or ""
            if entry.get("error"):
                law_source_lines.append(f"- 《{name}》：{entry['error']}")
                continue
            if entry.get("found"):
                cached = "（缓存）" if entry.get("from_cache") else ""
                law_source_lines.append(
                    f"- 《{entry.get('official_title') or name}》：官方库已收录，"
                    f"公布 {entry.get('publish_date') or '—'}、"
                    f"施行 {entry.get('effective_date') or '—'}"
                    f"，出处 {entry.get('detail_url') or '国家法律法规数据库'}{cached}"
                )
            else:
                law_source_lines.append(f"- 《{name}》：官方库未检索到，引用前需人工复核")

        sample_lines: list[str] = []
        case_citations = [item for item in verified if item.source_type == "case"]
        if assessment is not None and len(case_citations) != assessment.sample_size:
            sample_lines.append(
                f"检索命中 {len(case_citations)} 件类案，本次区间只采用其中 "
                f"{assessment.sample_size} 件（本地区优先，其余只作参照）。"
            )
        if assessment is not None and assessment.sample_size:
            sample_lines.append(f"样本构成：{assessment.sample_note}")
        if assessment is not None and assessment.has_synthetic:
            sample_lines.append(
                "注意：上述类案中有演示数据，仅用于演示检索与计算流程，不是真实裁判结果，不得作为法律依据。"
            )
        if assessment is not None and assessment.cross_region_sample_size:
            sample_lines.append(
                f"其中 {assessment.cross_region_sample_size} 件来自其他省份，"
                "各地裁判尺度存在差异，跨省区间只能作弱参照。"
            )

        official_lines: list[str] = []
        for item in record.extra.get("official_cases") or []:
            source = item.get("source_url") or ""
            fetched = (item.get("fetched_at") or "")[:10]
            official_lines.append(
                f"- {item.get('case_id')}｜{item.get('dispute')}\n"
                f"    案号：{item.get('case_no') or '（官方页面未列明）'}"
                f"；来源：{item.get('source_name') or '最高人民法院官网'}"
                f"（{source}{'，抓取于 ' + fetched if fetched else ''}）\n"
                f"    裁判要点：{(item.get('holding') or '')[:200]}"
            )
        if not official_lines:
            official_lines = [
                "- 未检索到可比的官方案例（真实来源）。"
                "注意：类案区间使用的是标注为演示数据的样本，两者不能混为一谈。"
            ]

        artifacts.append(
            Artifact(
                kind="research",
                title="法条与类案依据",
                body="\n".join(
                    ["法条依据："] + (law_lines or ["- 暂无通过核验的法条依据"])
                    + (["", "官方库核对（仅证明该法存在，不代替条文正文核验）："] + law_source_lines
                       if law_source_lines else [])
                    + ["", "类案参照："] + case_lines
                    + ["", "官方真实案例参照（最高人民法院指导性案例缓存）："] + official_lines
                    + ([""] + sample_lines if sample_lines else [])
                ),
            )
        )

        findings_list = profile.item_findings or []
        if findings_list:
            calc_lines = ["逐项认定结果（按法院的认定逻辑，逐项判断能否支持、按什么标准算）：", ""]
            for index, item in enumerate(findings_list, start=1):
                status = STATUS_LABEL.get(item.status, item.status)
                if item.status == "supported":
                    calc_lines.append(
                        f"{index}. {item.item}：主张 {item.claimed:.0f} 元 → 可以支持 "
                        f"{item.counted:.0f} 元"
                    )
                else:
                    calc_lines.append(
                        f"{index}. {item.item}：主张 {item.claimed:.0f} 元 → {status}，暂不计入金额"
                    )
                calc_lines.append(f"   依据：{item.basis}")
                if item.standard:
                    calc_lines.append(f"   标准：{item.standard}")
                if item.missing_params:
                    calc_lines.append(f"   还缺参数：{'、'.join(item.missing_params)}")
                    # 缺的参数凡是有官方标准的，直接给出真实数字与出处；
                    # 查不到标准（或地区不在表内）就维持原样，不硬凑数字
                    for param in item.missing_params:
                        standard_desc = self.standards.describe_param(param, region=profile.region)
                        if standard_desc:
                            calc_lines.append(f"   标准参考：{standard_desc}")
                if item.note:
                    calc_lines.append(f"   提示：{item.note}")
            counted = sum(item.counted for item in findings_list if item.status == "supported")
            calc_lines += [
                "",
                f"合计可认定：约 {counted:.0f} 元",
                f"再乘以责任比例 {profile.responsibility_ratio:.0%}，得到可支持金额（约数）。",
                "",
                "计算方式说明：",
                "· 按逐项认定逻辑计算，有凭证的按凭证金额认定，不因证据不足而给主张金额打折；",
                "· 事实存在但缺凭证、且法律规定了计算标准的，按法定标准计算，并列出所需参数；",
                "· 既无证据又无法定标准的项目，按不予支持处理（归零），而不是按比例折算；",
                "· 金额由确定性规则计算，不由大模型生成，保证可复现、可解释。",
                "· 结论里的金额均为按现有材料估算的约数（已用「约」标注），"
                "最终以证据和裁判认定为准。",
            ]
            artifacts.append(
                Artifact(
                    kind="calculation",
                    title="金额计算说明",
                    body="\n".join(calc_lines),
                )
            )

        artifacts.append(
            Artifact(
                kind="risk",
                title="复核意见与风险清单",
                body="\n".join(
                    f"[{f.severity.value}] {f.category}：{f.detail}（依据：{f.basis}）"
                    + ("　→ 已修订" if f.resolved else "")
                    for f in findings
                ),
            )
        )

        revisions = record.extra.get("revisions") or []
        revision_lines = [
            "多轮对抗的结果：复核意见提出后会逐条回应，不是一次定稿。",
            "",
        ]
        if not revisions:
            revision_lines.append(
                "首轮复核后没有未解决的中/高危问题，因此未触发第二轮修订"
                "（有需要修订的问题时才会追加一轮「修订 → 对方反驳 → 再复核」）。"
            )
        if revisions:
            for item in revisions:
                revision_lines.append(f"第 {item['round']} 轮修订：{item['summary']}")
                for change in item.get("changes") or []:
                    action = ACTION_LABEL.get(str(change.get("action")), str(change.get("action")))
                    revision_lines.append(
                        f"  · [{action}] {change.get('category')}：{change.get('detail')}"
                    )
                revision_lines.append("")
            unresolved = [
                item
                for item in findings
                if not item.resolved and item.severity in {Severity.HIGH, Severity.MEDIUM}
            ]
            revision_lines.append(
                "仍未解决（需要你补材料或人工核实）："
                + (
                    "；".join(f"{item.category}：{item.detail}" for item in unresolved)
                    or "无"
                )
            )
        artifacts.append(
            Artifact(
                kind="revision",
                title="修订说明与遗留问题",
                body="\n".join(revision_lines),
            )
        )

        artifacts.append(
            Artifact(
                kind="actions",
                title="维权路径对照",
                body="\n".join(
                    [
                        "1. 协商：成本最低，需准备证据清单与书面主张。",
                        "2. 投诉/调解：向主管部门或调解组织申请，周期较短。",
                        "3. 仲裁/诉讼：成本与周期上升，需确认时效与管辖。",
                        "提示：本方案为参考性质，不构成法律意见，重大金额或时效临近请咨询执业律师。",
                    ]
                ),
            )
        )

        # 模拟判决书：金额只取评估结果，类案只引真实来源案例；无类案时如实声明
        real_citations = [
            c
            for c in verified
            if c.source_type == "case" and c.grade == "authoritative"
        ]
        artifacts.append(
            Artifact(
                kind="document",
                title="模拟判决书（草稿，仅供学习参考）",
                body=render_judgment(profile, assessment, real_citations),
            )
        )
        return artifacts


def _fmt(value: float | None) -> str:
    return "无数据" if value is None else f"{value:.0f}"
