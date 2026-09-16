"""角色智能体。

当前版本用确定性规则实现，先把架构、工具边界和验收标准跑通。
接入大模型时，只需把 BaseAgent.decide 替换为真实的模型调用，
其余的角色隔离、工具白名单、预算与检查点机制保持不变。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .events import EventBus
from .models import (
    AgentResult,
    CaseProfile,
    CaseRecord,
    Citation,
    CriticFinding,
    LegalProvision,
    Role,
    Severity,
)
from .state import Budget
from .tools import ToolCall, ToolRouter


# 修订结论的三种处理方式：已修订 / 坚持原判断 / 本轮解决不了
ACTION_LABEL = {"fixed": "已修订", "kept": "坚持原判断", "cannot": "本轮解决不了"}


@dataclass
class AgentContext:
    """子任务合同：目标、范围、材料、预算、输出格式。"""

    task_id: str
    profile: CaseProfile
    legal_cap: float = 0.0
    expectation: float = 0.0
    extra: dict = field(default_factory=dict)


class BaseAgent:
    """最小 Agent Loop：规划 → 执行 → 观察 → 自检。"""

    role: Role
    display_name: str
    system_prompt: str

    def __init__(
        self,
        router: ToolRouter,
        bus: EventBus,
        budget: Budget,
        llm=None,
    ) -> None:
        self.router = router
        self.bus = bus
        self.budget = budget
        self.llm = llm
        self._llm_ok_reported = False
        self._task_id = ""

    def ask_json(self, system: str, user: str, max_tokens: int = 900) -> dict | None:
        """调用大模型并解析 JSON；不可用时返回 None，由调用方退回规则逻辑。"""

        if self.llm is None or not getattr(self.llm, "available", False):
            return None
        failures_before = getattr(self.llm, "failures", 0)
        try:
            payload = self.llm.chat_json(system, user, max_tokens=max_tokens)
        except Exception:  # noqa: BLE001
            payload = None
        self._report_llm_state(payload, failures_before)
        return payload

    def _report_llm_state(self, payload, failures_before: int) -> None:
        """模型一有结果就推一次状态：成功只报一次，失败每次都报。

        这样前端不会等整个任务跑完才知道「模型到底通没通」。
        """

        failures = getattr(self.llm, "failures", 0)
        calls = getattr(self.llm, "calls", 0)
        if failures > failures_before:
            self.bus.emit(
                "llm_state",
                self._task_id,
                {
                    "status": "degraded",
                    "text": f"大模型第 {failures} 次调用失败，该环节已退回规则版",
                },
            )
            return
        if payload and not self._llm_ok_reported:
            self._llm_ok_reported = True
            self.bus.emit(
                "llm_state",
                self._task_id,
                {"status": "online", "text": f"大模型调用正常（已调用 {calls} 次）"},
            )

    def run(self, context: AgentContext) -> AgentResult:
        self._task_id = context.task_id
        self.bus.emit(
            "agent_started",
            context.task_id,
            {"agent": self.role.value, "name": self.display_name},
        )
        self.budget.spend_turn()
        try:
            result = self.act(context)
        except Exception as exc:  # noqa: BLE001 - 单个角色失败不应中断整体任务
            self.bus.emit(
                "agent_message",
                context.task_id,
                {"agent": self.role.value, "status": "failed", "detail": str(exc)},
            )
            return AgentResult(
                agent=self.role.value,
                status="failed",
                summary=f"角色执行失败：{exc}",
                open_questions=["该角色结果缺失，最终报告需标注不确定性"],
            )
        self.bus.emit(
            "agent_message",
            context.task_id,
            {"agent": self.role.value, "status": result.status, "summary": result.summary},
        )
        return result

    def act(self, context: AgentContext) -> AgentResult:  # pragma: no cover - 抽象方法
        raise NotImplementedError

    def call_tool(self, tool: str, **arguments):
        self.budget.spend_tool_call()
        return self.router.call(self.role, ToolCall(tool=tool, arguments=arguments))

    # ------------------------------------------------------------------ 过程可见
    def emit_delta(
        self,
        context: AgentContext,
        text: str,
        index: int | None = None,
        total: int | None = None,
        kind: str = "bullet",
    ) -> None:
        """把角色的输出拆成增量事件推给前端，用于逐句呈现。"""

        self.bus.emit(
            "agent_delta",
            context.task_id,
            {
                "agent": self.role.value,
                "name": self.display_name,
                "text": text,
                "index": index,
                "total": total,
                "kind": kind,
            },
        )

    def emit_deltas(self, context: AgentContext, bullets: list[str]) -> None:
        total = len(bullets)
        for index, text in enumerate(bullets, start=1):
            self.emit_delta(context, text, index=index, total=total)


class ResearcherAgent(BaseAgent):
    """检索书记员：只负责查法条、查类案、核对来源，不产出结论。"""

    role = Role.RESEARCHER
    display_name = "检索书记员"
    system_prompt = "你只负责检索与核对来源，不得编造任何法条或案号。"

    def act(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        keywords = [profile.cause] + profile.disputed_points[:3]
        self.bus.emit("retrieval_started", context.task_id, {"agent": self.role.value})
        for keyword in keywords[:2]:
            self.bus.emit(
                "retrieval_query",
                context.task_id,
                {"scope": "law", "query": keyword, "detail": "内置法条库"},
            )
        self.bus.emit(
            "retrieval_query",
            context.task_id,
            {
                "scope": "case",
                "query": profile.cause,
                "detail": "、".join(profile.available_evidence) or "按案由与争议焦点匹配",
            },
        )

        # 法条与类案两条检索相互独立：并发发起，将来接真实数据源时不会被对方拖慢
        with ThreadPoolExecutor(max_workers=2) as pool:
            law_future = pool.submit(self.call_tool, "law_lookup", keywords=keywords)
            case_future = pool.submit(
                self.call_tool,
                "case_search",
                case_type=profile.cause,
                region=profile.region,
                evidence=list(profile.available_evidence),
            )
            provisions: list[LegalProvision] = law_future.result()
            cases: list[CaseRecord] = case_future.result()

        for index, provision in enumerate(provisions, start=1):
            self.bus.emit(
                "retrieval_hit",
                context.task_id,
                {
                    "scope": "law",
                    "index": index,
                    "total": len(provisions),
                    "label": provision.label,
                    "source": provision.source_url or "内置法条库",
                    "verified": provision.verified,
                },
            )
        for index, case in enumerate(cases, start=1):
            self.bus.emit(
                "retrieval_hit",
                context.task_id,
                {
                    "scope": "case",
                    "index": index,
                    "total": len(cases),
                    "label": case.label,
                    "source": case.source_url or case.court,
                    "synthetic": case.synthetic,
                },
            )

        # 真实官方案例（最高人民法院指导性案例缓存）：只作裁判要点参照，不参与金额区间
        official_cases = self._search_official_cases(context, keywords)

        # 第二条检索链路：拿命中的法条去官方库（国家法律法规数据库）核对收录情况。
        # 这条只证明「这部法存在、何时公布施行」，不代表条文正文已核验。
        law_sources = self._check_official_sources(context, provisions)
        self.bus.emit(
            "retrieval_finished",
            context.task_id,
            {"provisions": len(provisions), "cases": len(cases)},
        )

        citations = [
            Citation(
                source_type="law",
                source_id=item.provision_id,
                label=item.label,
                quote=item.text[:40],
            )
            for item in provisions
        ]
        citations += [
            Citation(
                source_type="case",
                source_id=case.case_id,
                label=case.label,
                quote=case.holding[:40],
            )
            for case in cases
        ]

        bullets = [f"命中法条 {len(provisions)} 条", f"命中类案 {len(cases)} 件"]
        if not cases:
            bullets.append("未检索到同类案例：最终报告必须标注样本不足")
        self.emit_delta(context, "检索完成，未产出结论", kind="summary")
        self.emit_deltas(context, bullets)
        return AgentResult(
            agent=self.role.value,
            status="completed",
            summary="检索完成",
            bullets=bullets,
            citations=citations,
            open_questions=[] if cases else ["缺少同类案例，实践区间无法计算"],
            payload={
                "provisions": provisions,
                "cases": cases,
                "law_sources": law_sources,
                "official_cases": official_cases,
            },
        )

    def _search_official_cases(self, context: AgentContext, keywords: list[str]) -> list:
        """在本地缓存的真实官方案例里找参照；用的是抓取时留下的真实来源与案号。"""

        if "official_case_search" not in self.router.registry.names():
            return []
        try:
            found = self.call_tool(
                "official_case_search",
                keywords=list(keywords),
                case_type=context.profile.cause,
            )
        except Exception:  # noqa: BLE001 - 缓存缺失或损坏都不能中断检索
            return []
        for index, case in enumerate(found, start=1):
            self.bus.emit(
                "retrieval_hit",
                context.task_id,
                {
                    "scope": "case_official",
                    "index": index,
                    "total": len(found),
                    "label": f"{case.case_id}：{case.dispute[:40]}",
                    "source": case.source_url,
                    "detail": "；".join(
                        item
                        for item in [
                            case.case_no,
                            f"抓取于 {case.fetched_at[:10]}" if case.fetched_at else "",
                        ]
                        if item
                    ),
                },
            )
        if found:
            self.emit_delta(
                context,
                f"真实官方案例命中 {len(found)} 篇（含案号与来源链接）",
                kind="summary",
            )
        return found

    def _check_official_sources(self, context: AgentContext, provisions) -> list:
        """逐部法律回源核对，最多查两部，任何失败都不影响检索主流程。"""

        if "law_source_check" not in self.router.registry.names():
            return []

        law_names: list[str] = []
        for provision in provisions:
            name = provision.law_name
            if name and name not in law_names:
                law_names.append(name)

        checks: list = []
        for law_name in law_names[:2]:
            self.bus.emit(
                "retrieval_query",
                context.task_id,
                {"scope": "law_web", "query": law_name, "detail": "国家法律法规数据库"},
            )
            try:
                check = self.call_tool("law_source_check", law_name=law_name)
            except Exception as exc:  # noqa: BLE001 - 官方库不可用不能中断检索
                del exc
                continue
            # 未启用或网络不可用（且没有缓存）时不记这一条：
            # 离线演示不该在过程流里刷一堆「核对失败」，那只会稀释真正的信息
            if not check.found and check.error:
                continue
            checks.append(check)
            self.bus.emit(
                "retrieval_hit",
                context.task_id,
                {
                    "scope": "law_web",
                    "label": f"《{check.official_title or law_name}》",
                    "source": check.detail_url or "国家法律法规数据库",
                    "detail": check.summary + ("（缓存）" if check.from_cache else ""),
                    "tone": "hit" if check.found else "warn",
                },
            )
        if checks:
            found = sum(1 for item in checks if item.found)
            self.emit_delta(
                context,
                f"官方库核对完成：{found}/{len(checks)} 部法律在库中收录",
                kind="summary",
            )
        return checks


# 两套完全不同的办案思路：原告想的是「怎么把能拿的拿到」，
# 被告想的是「怎么把该给的不给」。共用一套机制，但目标、清单、禁区都不一样。
STANCE_PLAYBOOK: dict[Role, dict[str, object]] = {
    Role.CLAIMANT: {
        "title": "原告 / 主张方",
        "system": (
            "你是原告方的代理律师，负责把委托人的损失做实、把能主张的都要到位。"
            "只能引用给定法条，不得编造法条、案号或金额；不得承诺胜诉或具体赔偿结果。"
        ),
        "goal": (
            "目标：把损失做实、把可主张的项目找全、把责任比例与赔偿基数顶到法律允许的上限，"
            "并规划举证顺序与谈判锚点。"
        ),
        "sections": ["可主张项目", "举证顺序", "责任比例", "谈判锚点"],
        "must": [
            "逐项列出可以主张的赔偿项目：每一项给法律依据（条号）+ 计算口径 + 现有材料能支撑多少",
            "缺证据的项目要写成「补什么材料就能主张、大概能拿多少」，而不是「主张不成立」",
            "按「先补哪一项最划算」给出举证顺序（影响金额大的排前面）",
            "预判对方会拿责任比例、必要性、因果关系做文章，给出你的应对说法",
            "给出协商与诉讼的锚点金额、让步空间与最低可接受线",
        ],
        "forbidden": [
            "不要替对方论证，也不要主动否定自己可主张的项目",
            "不要把「对方可能抗辩的点」写成结论，要写成预案",
        ],
    },
    Role.DEFENDANT: {
        "title": "被告 / 抗辩方",
        "system": (
            "你是被告方的代理律师，负责逐项打掉对方主张中站不住的部分。"
            "只能引用给定法条，不得编造法条、案号或金额；不得承诺判决结果。"
        ),
        "goal": (
            "目标：逐项质证对方证据与法律适用，压低责任比例与赔偿基数，"
            "说清自己最坏大概赔多少、以及和解区间在哪。"
        ),
        "sections": ["质证要点", "举证责任", "责任比例", "和解区间"],
        "must": [
            "逐项质证：对方每一项主张的证据是否真实、合法、与本案有关联，不认可的要说明理由",
            "指出哪些项目的举证责任在对方，以及举证不能的后果",
            "核对责任比例认定，指出可主张减轻责任的事实与依据（如认定书可复核、对方也有过错）",
            "给出自己可以接受的赔付上限与和解方案，并说明为什么这个数合适",
        ],
        "forbidden": [
            "不要替对方补强证据，也不要默认认可对方未证明的部分",
            "不要只说「对方没证据」，还要说明自己该准备什么材料",
        ],
    },
}


class AdvocacyAgent(BaseAgent):
    """主张方 / 抗辩方：同一套机制，两套完全不同的办案思路。"""

    def __init__(self, stance: Role, **kwargs) -> None:
        super().__init__(**kwargs)
        self.role = stance
        playbook = STANCE_PLAYBOOK[stance]
        self.display_name = "主张方" if stance == Role.CLAIMANT else "抗辩方"
        self.playbook = playbook
        self.system_prompt = str(playbook["system"])

    def act(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        provisions: list[LegalProvision] = self.call_tool(
            "law_lookup", keywords=[profile.cause] + profile.disputed_points[:2]
        )
        self.emit_delta(
            context,
            f"正在研读法条与证据，准备{self.display_name}论证…",
            kind="status",
        )
        result = self._llm_arguments(context, provisions) or self._rule_arguments(
            context, provisions
        )
        self.emit_delta(context, result.summary, kind="summary")
        self.emit_deltas(context, result.bullets)
        return result

    def _rule_arguments(self, context: AgentContext, provisions) -> AgentResult:
        """规则版论证：模型不可用时的兜底，保证主流程与输出结构不变。"""

        profile = context.profile
        bullets: list[str] = []
        laws = "、".join(item.label for item in provisions[:3]) or "暂无法条"
        cap = context.legal_cap * profile.responsibility_ratio
        missing = "、".join(profile.missing_evidence) or "无"
        if self.role == Role.CLAIMANT:
            bullets.append(
                f"【可主张项目】按 {profile.cause} 逐项主张，现有材料能支撑的合计约 "
                f"{context.legal_cap:.0f} 元；可援引 {laws}"
            )
            bullets.append(
                f"【举证顺序】先补影响金额最大的项目：{missing}；"
                "其余项目按「金额 × 补齐难度」依次补"
            )
            bullets.append(
                f"【责任比例】按目前认定按 {profile.responsibility_ratio:.0%} 主张，"
                f"对应约 {cap:.0f} 元；若对方主张你方有过错，用现有认定材料反驳"
            )
            bullets.append(
                f"【谈判锚点】以约 {cap:.0f} 元为底线、{context.expectation:.0f} 元为起点协商，"
                "证据补齐后再调整口径"
            )
        else:
            bullets.append(
                f"【质证要点】对方主张的项目中，{missing} 尚未提供对应证据，"
                "不能仅凭陈述计入损失"
            )
            bullets.append(
                "【举证责任】上述项目的举证责任在对方，拿不出证据的后果由对方承担；"
                "可要求对方出示原件并说明来源"
            )
            bullets.append(
                f"【责任比例】应按 {profile.responsibility_ratio:.0%} 计算，"
                "超出部分不应支持；认为认定有误时在复核期内申请复核"
            )
            bullets.append(
                f"【和解区间】以约 {cap:.0f} 元为可接受上限谈和解，"
                f"对方主张的 {context.expectation:.0f} 元超出部分缺乏依据"
            )
        return AgentResult(
            agent=self.role.value,
            status="completed",
            summary=f"{self.display_name}论证完成",
            bullets=bullets,
        )

    def _llm_arguments(self, context: AgentContext, provisions) -> AgentResult | None:
        profile = context.profile
        playbook = self.playbook
        sections = "、".join(str(item) for item in playbook["sections"])
        must = "\n".join(f"- {item}" for item in playbook["must"])
        forbidden = "\n".join(f"- {item}" for item in playbook["forbidden"])
        law_text = "\n".join(
            f"- {item.label}：{item.text[:120]}" for item in provisions[:4]
        ) or "（本次未检索到可用法条）"
        amount_label = (
            f"我方主张金额（用户期望）：{context.expectation:.0f} 元"
            if self.role == Role.CLAIMANT
            else f"对方主张金额：{context.expectation:.0f} 元"
        )
        extra_block = ""
        if context.extra.get("revision_summary"):
            extra_block = (
                "\n对方本轮修订说明：" + str(context.extra["revision_summary"])[:400]
                + "\n这是第二轮对抗：请针对对方修订后的说法再打一轮，"
                "只讲仍然站不住的部分，并说明哪些原来被指出的问题对方已经补救。\n"
            )
        user = (
            f"你的身份：{playbook['title']}\n"
            f"{playbook['goal']}\n"
            f"案由：{profile.cause}\n"
            f"用户原话：{profile.description or profile.summary}\n"
            f"责任比例：{profile.responsibility_ratio:.0%}\n"
            f"现有证据：{'、'.join(profile.available_evidence) or '无'}\n"
            f"缺失证据：{'、'.join(profile.missing_evidence) or '无'}\n"
            f"按现有材料可支持的金额（估算，用「约」表述）：约 {context.legal_cap:.0f} 元\n"
            f"{amount_label}\n"
            f"可用法条：\n{law_text}\n\n"
            f"必须覆盖这几个方面，每条要点用【方面】开头：{sections}\n"
            f"{must}\n"
            f"禁止：\n{forbidden}\n"
            "金额一律用「约 X 元」的估算口径，不要写成确定结论；不得承诺胜诉。\n"
            f"{extra_block}"
            '输出 JSON：{"summary": "一句话结论（站在本立场）", '
            '"bullets": ["【方面】要点1", "【方面】要点2"]}'
        )
        payload = self.ask_json(
            str(playbook["system"]),
            user,
            max_tokens=1800,
        )
        if not payload:
            return None
        bullets = [str(item) for item in (payload.get("bullets") or [])][:8]
        if not bullets:
            return None
        bullets = self._ensure_sections(context, provisions, bullets)
        return AgentResult(
            agent=self.role.value,
            status="completed",
            summary=str(payload.get("summary") or f"{self.display_name}论证完成"),
            bullets=bullets,
        )

    def _ensure_sections(
        self, context: AgentContext, provisions, bullets: list[str]
    ) -> list[str]:
        """保证四个视角都写到：模型漏掉的方面，用规则版那条补上。

        实测过模型会连写六条「可主张项目」而完全不提举证顺序与谈判锚点，
        只靠提示词约束不住，所以这里做一次确定性兜底。
        """

        required = [str(item) for item in self.playbook["sections"]]
        covered = {
            section for section in required if any(section in bullet for bullet in bullets)
        }
        missing = [section for section in required if section not in covered]
        if not missing:
            return bullets
        for bullet in self._rule_arguments(context, provisions).bullets:
            label = ""
            if bullet.startswith("【") and "】" in bullet:
                label = bullet[1 : bullet.index("】")]
            if label in missing:
                bullets.append(bullet)
                missing.remove(label)
            if not missing:
                break
        return bullets


class JudgeAgent(BaseAgent):
    """法官视角：只做中立评估与风险提示，不作最终裁判。"""

    role = Role.JUDGE
    display_name = "法官视角"
    system_prompt = "你保持中立，只做评估与风险提示，不给出胜诉败诉的确定结论。"

    def act(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        completeness = float((profile.extra or {}).get("sufficiency", 0.0))
        self.emit_delta(context, "正在对照双方论证，做中立评估…", kind="status")
        payload = self.ask_json(
            "你是中立的评估者，只做证据与论证质量的评估，不给出胜诉败诉的确定结论。",
            (
                f"案由：{profile.cause}\n"
                f"用户原话：{profile.description or profile.summary}\n"
                f"责任比例：{profile.responsibility_ratio:.0%}\n"
                f"现有证据：{'、'.join(profile.available_evidence) or '无'}\n"
                f"缺失证据：{'、'.join(profile.missing_evidence) or '无'}\n"
                f"主张金额：{context.legal_cap:.0f} 元\n"
                '输出 JSON：{"summary": "一句话评估", "bullets": ["中立评估要点"]}'
            ),
        )
        if payload and payload.get("bullets"):
            result = AgentResult(
                agent=self.role.value,
                status="completed",
                summary=str(payload.get("summary") or "中立评估完成"),
                bullets=[str(item) for item in payload["bullets"]][:8],
            )
        else:
            bullets = [
                f"材料齐备度约 {completeness:.0%}，缺口集中在："
                + ("、".join(profile.missing_evidence) or "无"),
                "双方争议焦点：" + ("、".join(profile.disputed_points) or "尚未明确"),
                "本评估只用于学习与参考，不构成裁判结论",
            ]
            result = AgentResult(
                agent=self.role.value,
                status="completed",
                summary="中立评估完成",
                bullets=bullets,
            )
        self.emit_delta(context, result.summary, kind="summary")
        self.emit_deltas(context, result.bullets)
        return result


class RevisionAgent(BaseAgent):
    """方案修订：拿到复核意见后逐条回应「已修订 / 坚持 / 无法处理」。

    多轮对抗的第一棒。它不重新论证立场，只负责把复核意见逐条消化，
    避免出现「提了问题却没人回应」的假迭代。
    """

    display_name = "方案修订"

    def __init__(self, stance: Role, **kwargs) -> None:
        super().__init__(**kwargs)
        self.role = stance
        self.system_prompt = (
            "你负责按复核意见修订方案，逐条说明已修订、坚持与无法处理的理由，"
            "不得编造法条、案号或金额。"
        )

    def act(self, context: AgentContext) -> AgentResult:
        findings = list(context.extra.get("open_findings") or [])
        self.emit_delta(
            context, f"正在按 {len(findings)} 条复核意见修订方案…", kind="status"
        )
        payload = self._llm_changes(context, findings) or self._rule_changes(findings)
        changes = payload.get("changes") or []
        bullets = [
            f"{ACTION_LABEL.get(item.get('action'), item.get('action'))}"
            f" {item.get('category')}：{item.get('detail')}"
            for item in changes
        ]
        result = AgentResult(
            agent=self.role.value,
            status="completed",
            summary=str(payload.get("summary") or f"逐条回应了 {len(changes)} 条复核意见"),
            bullets=bullets,
            payload={"changes": changes},
        )
        self.emit_delta(context, result.summary, kind="summary")
        self.emit_deltas(context, result.bullets)
        return result

    def _llm_changes(self, context: AgentContext, findings: list) -> dict | None:
        listing = (
            "\n".join(
                f"- [{item.severity.value}] {item.category}：{item.detail}（依据：{item.basis}）"
                for item in findings
            )
            or "（本轮没有未处理的复核意见）"
        )
        payload = self.ask_json(
            "你是方案修订助手。针对复核意见逐条给出处理结论，"
            "不得编造法条、案号或金额，金额类结论只能引用给定数据。",
            (
                f"案由：{context.profile.cause}\n"
                f"用户原话：{context.profile.description or context.profile.summary}\n"
                f"现有证据：{'、'.join(context.profile.available_evidence) or '无'}\n"
                f"缺失证据：{'、'.join(context.profile.missing_evidence) or '无'}\n"
                f"可支持金额：{context.legal_cap:.0f} 元\n"
                f"复核意见：\n{listing}\n\n"
                "输出 JSON：{\"summary\": \"一句话概括本轮修订\", \"changes\": "
                "[{\"category\": \"对应的复核意见类别\", "
                "\"action\": \"fixed(已修订) / kept(坚持原判断) / cannot(本轮解决不了)\", "
                "\"detail\": \"怎么改的，或为什么坚持，或为什么解决不了\"}]}"
            ),
            max_tokens=900,
        )
        if not payload or not payload.get("changes"):
            return None
        changes: list[dict] = []
        for entry in (payload.get("changes") or [])[:8]:
            if not isinstance(entry, dict):
                continue
            action = str(entry.get("action") or "kept").strip().lower()
            if action not in ACTION_LABEL:
                action = "kept"
            changes.append(
                {
                    "category": str(entry.get("category") or "复核意见"),
                    "action": action,
                    "detail": str(entry.get("detail") or ""),
                }
            )
        if not changes:
            return None
        return {
            "summary": str(payload.get("summary") or f"逐条回应了 {len(changes)} 条复核意见"),
            "changes": changes,
        }

    def _rule_changes(self, findings: list) -> dict:
        """规则版：不假装能修，按问题性质给出诚实的处理结论。"""

        mapping = {
            "证据缺口": ("cannot", "需要用户补充材料，工具无法自行取得"),
            "金额未计入": ("cannot", "缺证据的项目只能等补齐后再计入，不能靠折算"),
            "金额缺失": ("cannot", "用户没有给出金额，需要补充后才能计算"),
            "样本不足": ("cannot", "真实类案样本不足，只能标注参考价值有限"),
            "法条来源存疑": ("cannot", "官方库未收录该法，需人工复核后再判断"),
            "引用核验": ("fixed", "未通过核验的引用已从交付物中剔除"),
            "时效提示": ("fixed", "已把时效核对写入行动方案的第一步"),
            "诉求超出法定上限": ("fixed", "结论已按现有证据计算的上限口径给出，并写明差距原因"),
        }
        changes = []
        for finding in findings:
            action, detail = mapping.get(
                finding.category, ("kept", "保留原判断，待用户补充材料后再复核")
            )
            changes.append(
                {"category": finding.category, "action": action, "detail": detail}
            )
        return {
            "summary": f"规则版逐条回应了 {len(changes)} 条复核意见",
            "changes": changes,
        }


class RedTeamAgent(BaseAgent):
    """质证复核（内部角色键仍为 redteam）：独立复核主张，只挑毛病，不生成新结论。

    命名说明：法律语境里「质证」指对证据的真实性、合法性、关联性进行质辩，
    「复核」表示独立于主张方的二次审查，比「红队挑刺」更贴近业务语言。
    """

    role = Role.REDTEAM
    display_name = "质证复核"
    system_prompt = "你的唯一职责是复核并挑出漏洞，每条必须挂上依据。"

    def act(self, context: AgentContext) -> AgentResult:
        findings: list[CriticFinding] = list(context.extra.get("findings", []))
        self.emit_delta(
            context, "正在逐条复核：证据、法条适用、时效、金额…", kind="status"
        )
        payload = self.ask_json(
            "你是质证复核员，只指出用户主张中的漏洞，每条必须给出依据，"
            "不得编造法条或案号，不得重复已有的发现。",
            (
                f"案由：{context.profile.cause}\n"
                f"用户原话：{context.profile.description or context.profile.summary}\n"
                f"现有证据：{'、'.join(context.profile.available_evidence) or '无'}\n"
                f"缺失证据：{'、'.join(context.profile.missing_evidence) or '无'}\n"
                f"已有发现：{'；'.join(f'{f.category}：{f.detail}' for f in findings) or '无'}\n"
                '输出 JSON：{"findings": [{"category": "类别", "severity": "low/medium/high", '
                '"detail": "漏洞描述", "basis": "依据"}]}'
            ),
        )
        extra: list[CriticFinding] = []
        if payload:
            for entry in (payload.get("findings") or [])[:4]:
                try:
                    severity = Severity(str(entry.get("severity", "medium")).lower())
                except ValueError:
                    severity = Severity.MEDIUM
                extra.append(
                    CriticFinding(
                        category=str(entry.get("category") or "补充审查"),
                        severity=severity,
                        detail=str(entry.get("detail") or ""),
                        basis=f"模型提示，需核验：{entry.get('basis') or '模型补充审查'}",
                    )
                )
        # 由编排层检测出的问题会用 critic_finding 事件单独推送，
        # 这里只逐条推送模型补充的发现，避免同一条漏洞在过程流里出现两次
        for index, finding in enumerate(extra, start=1):
            self.emit_delta(
                context,
                f"[{finding.severity.value}] {finding.category}：{finding.detail}",
                index=index,
                total=len(extra),
            )
        self.emit_delta(context, f"提出 {len(findings) + len(extra)} 条漏洞", kind="summary")
        return AgentResult(
            agent=self.role.value,
            status="completed",
            summary=f"提出 {len(findings) + len(extra)} 条漏洞",
            bullets=[f"[{f.severity.value}] {f.category}：{f.detail}" for f in findings],
            payload={"extra_findings": extra},
        )


AGENT_FACTORY: dict[str, Callable[..., BaseAgent]] = {
    "researcher": ResearcherAgent,
    "claimant": lambda **kw: AdvocacyAgent(Role.CLAIMANT, **kw),
    "defendant": lambda **kw: AdvocacyAgent(Role.DEFENDANT, **kw),
    "revision": RevisionAgent,
    "judge": JudgeAgent,
    "redteam": RedTeamAgent,
}
