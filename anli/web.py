"""最小可用的 Web 界面：三栏布局 + 阶段进度流。

设计取舍：进度推送用 SSE，而不是 WebSocket。
本场景只需要服务端单向推事件（阶段、工具调用、核验结果），
SSE 更简单，不需要心跳与重连管理；需要双向交互时再升级到 WebSocket。
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import uuid
import webbrowser
import base64
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .events import Event
from . import __version__
from .evidence_chain import QUALITY_QUESTIONS
from .intake import extract_expectation
from .llm import LLMClient, candidate_env_files
from .llm_intake import build_profile_llm
from .models import Role, TaskState
from .orchestrator import Orchestrator
from .standards import StandardsStore
from .exporter import build_markdown_export, export_model_from_record, export_model_from_view, render_markdown
from .docx_export import build_docx
from urllib.parse import parse_qs, quote as _urlquote, urlparse
from .paths import resource_path, user_root
from .retrieval_flk import LawSourceChecker
from .regions import DEFAULT_REGION, REGIONS, normalize_region
from .scope import detect_out_of_scope
from .scenarios import (
    car_accident_description,
    car_accident_files,
    consumption_description,
    consumption_files,
)
from .state import StateStore
from .stores import CaseStore, LawStore

ROOT = user_root()
DATA = resource_path("data")
WEB = resource_path("web")

SAMPLES = {
    "car": (car_accident_description, car_accident_files),
    "consumption": (consumption_description, consumption_files),
}

TASKS: dict[str, dict] = {}
# 最近一次任务的模型使用结果：用于页面刷新后仍能显示真实状态
# （/api/health 只能判断「有没有配置密钥」，测不出连通性）
LAST_LLM_RUN: dict = {}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# 对话轮次上限：初始提交算第 1 轮，之后最多再补 3 轮
MAX_ROUNDS = 4
_INDEX_CACHE: dict[str, str] = {}
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def build_view(record, report: str = "", error: str = "") -> dict:
    """把任务结果整理成前端要的结构；检查点也存这一份，重启后可恢复。"""

    # 转人工且用户还没确认时，行动方案不能出现在返回结果里（闸门要真的挡住）
    human_pending = bool(
        record and record.extra.get("human_review") and not record.extra.get("approved")
    )
    # 超出覆盖范围（刑事/行政）：连「民事分析」的字段都不返回。
    # 之前这里会把「案由：一般民事纠纷 / 责任比例：100% / 争议焦点」这类民事分析
    # 一起发给前端，等于对刑事案件做了民事分析，既没意义也踩红线。
    out_of_scope = bool(record and record.extra.get("out_of_scope"))
    return {
        "code": 0,
        "state": record.state.value,
        "role": record.role.value,
        "error": error,
        "summary": record.extra.get("summary", "") if record else "",
        "strategies": []
        if human_pending or out_of_scope
        else [
            asdict(item) for item in (record.extra.get("strategies") or [])
        ]
        if record
        else [],
        "assessment": asdict(record.assessment)
        if record and record.assessment and not out_of_scope
        else None,
        "item_findings": [
            asdict(item)
            for item in (record.profile.item_findings if record and record.profile else [])
        ]
        if not out_of_scope
        else [],
        "understanding": {
            "cause": record.profile.cause,
            "region": record.profile.region or DEFAULT_REGION,
            "responsibility_ratio": record.profile.responsibility_ratio,
            "claim_items": record.profile.claim_items,
            "claimed_items": {
                item.item: item.claimed for item in (record.profile.item_findings or [])
            },
            "evidence": record.profile.evidence_sources,
            "timeline": record.profile.timeline,
            "disputed_points": record.profile.disputed_points,
        }
        if record and record.profile and not out_of_scope
        else None,
        "evidence_request": record.extra.get("evidence_request", [])
        if record and not out_of_scope
        else [],
        "law_sources": record.extra.get("law_sources", []) if record else [],
        "official_cases": record.extra.get("official_cases", []) if record else [],
        "evidence_sufficiency": record.extra.get("evidence_sufficiency")
        if record
        else None,
        "intake_source": record.extra.get("intake_source", "") if record else "",
        "llm_status": record.extra.get("llm_status", "") if record else "",
        "llm_status_text": record.extra.get("llm_status_text", "") if record else "",
        "llm_calls": record.extra.get("llm_calls", 0) if record else 0,
        "llm_failures": record.extra.get("llm_failures", 0) if record else 0,
        "llm_retries": record.extra.get("llm_retries", 0) if record else 0,
        "llm_last_failure": record.extra.get("llm_last_failure", "") if record else "",
        "report": report,
        "findings": [asdict(item) for item in record.findings] if record else [],
        "citations": [asdict(item) for item in record.citations] if record else [],
        "human_review": record.extra.get("human_review", []) if record else [],
        "human_channels": record.extra.get("human_channels", []) if record else [],
        "out_of_scope": (record.extra.get("out_of_scope") or None) if record else None,
        "revisions": record.extra.get("revisions", []) if record else [],
        "rounds": record.extra.get("rounds", []) if record else [],
        "max_rounds": MAX_ROUNDS,
        "approved": bool(record.extra.get("approved")) if record else False,
    }


def render_index() -> str:
    """把 Bootstrap 样式内联进页面。

    之前用外链，浏览器一旦在服务重启的瞬间请求样式就会失败，
    整页变成裸 HTML。内联之后页面不再有第二个请求，样式不可能丢。
    """

    path = WEB / "index.html"
    mtime = path.stat().st_mtime
    if _INDEX_CACHE.get("mtime") == mtime and "html" in _INDEX_CACHE:
        return _INDEX_CACHE["html"]
    html = path.read_text(encoding="utf-8")
    link = '<link rel="stylesheet" href="/vendor/bootstrap.min.css">'
    css_path = WEB / "vendor" / "bootstrap.min.css"
    if link in html and css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        html = html.replace(link, "<style>\n" + css + "\n</style>")
    _INDEX_CACHE["html"] = html
    _INDEX_CACHE["mtime"] = mtime
    return html


def _build_orchestrator(
    on_event, llm=None, llm_configured: bool | None = None
) -> Orchestrator:
    # 官方库核对：离线环境用 ANLI_DISABLE_WEB=1 关掉，此时干脆不注册该工具
    checker = None
    if os.environ.get("ANLI_DISABLE_WEB") != "1":
        checker = LawSourceChecker(cache_path=ROOT / "cache" / "law_sources.json")
    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(
            DATA / "cases.seed.json",
            DATA / "cases.official.json",
            DATA / "real_cases.json",
        ),
        state_store=StateStore(ROOT / "state"),
        llm=llm,
        law_checker=checker,
        llm_configured=llm_configured,
        standards_store=StandardsStore.load(DATA / "standards.json"),
    )
    orchestrator.subscribe(on_event)
    return orchestrator


def _load_checkpoint(task_id: str) -> dict | None:
    """从落盘的检查点取回前端视图；取不到返回 None。"""

    payload = _load_checkpoint_payload(task_id)
    if not payload:
        return None
    view = payload.get("view")
    return view if isinstance(view, dict) else None


def health_payload() -> dict:
    """健康检查：把「配置层」和「运行层」两个状态一起给出。

    只看「有没有密钥」会自相矛盾：密钥配着但调用全失败时，
    页面会同时显示「大模型已连接」和「已掉线」。所以这里把最近一次真实运行的结果带上，
    运行层优先——最近一次跑失败了，就不要宣称已连接。
    """

    disabled = os.environ.get("ANLI_DISABLE_LLM") == "1"
    llm = None if disabled else LLMClient()
    last = dict(LAST_LLM_RUN) if LAST_LLM_RUN else None
    configured = bool(llm and llm.available)
    if disabled:
        status = "offline"
    elif last and last.get("status") in {"degraded", "offline"}:
        status = last["status"]
    elif last and last.get("status") == "online":
        status = "online"
    elif last and last.get("status") == "skipped":
        # 上次任务是「提前结束、没调模型」：既不是掉线，也还没验证过连通性
        status = "skipped"
    elif configured:
        status = "configured"
    else:
        status = "offline"
    return {
        "code": 0,
        "version": __version__,
        "llm": configured,
        "configured": configured,
        "disabled": disabled,
        "status": status,
        "model": llm.model if llm else "",
        "last_run": last,
        "env_files": [str(item) for item in candidate_env_files()],
        "data_dir": str(user_root()),
    }


def llm_status_for(
    llm_configured: bool, calls: int, failures: int, stopped_early: bool,
    last_failure: str = "",
) -> tuple[str, str]:
    """判定一次任务的模型使用状态，返回 (status, 说明文字)。

    status 取值：
    - `offline`    未配置密钥 / 已用 ANLI_DISABLE_LLM=1 关闭；
    - `degraded`   发起了调用但有失败；
    - `skipped`    **一次调用都没发起**（流程在调模型之前就结束了，例如超范围拦停、信息不足）；
    - `online`     调用成功且没有失败。

    `skipped` 是这次修的关键：刑事案件、信息不足这类"提前结束"的任务，
    llm_calls 同样是 0，之前被误判成「大模型调用全部失败，已自动退回规则版」，
    于是页面一边显示「大模型已连接」、一边显示「已掉线」，自相矛盾。
    """

    if not llm_configured:
        return "offline", "未连接大模型（未配置密钥或已用 ANLI_DISABLE_LLM=1 关闭），本次为规则版分析"
    if failures:
        text = (
            f"大模型有 {failures} 次调用失败，相关环节已退回规则版；其余 {calls} 次调用正常"
            if calls
            else "大模型调用全部失败，已自动退回规则版分析；请检查密钥、网络或模型名"
        )
        if last_failure:
            text += f"。最近失败原因：{last_failure}"
        return "degraded", text
    if calls == 0:
        return (
            "skipped",
            "本次流程在调用大模型之前就结束了（例如超出覆盖范围或信息不足），未使用大模型",
        )
    return "online", f"大模型版分析（调用 {calls} 次）"


def plan_intake(description: str) -> tuple[bool, str]:
    """开跑前先判断：这次要不要调用大模型做案情理解。

    超范围（刑事/行政）的咨询在分析阶段一定会被拦停，
    那就没必要先花一次模型调用去做案情理解——直接走规则版理解 + 拦停提示。
    这样「本次未调用大模型」这句话从一开始就是真的，不用等跑完才知道。
    """

    scope = detect_out_of_scope(description or "")
    if scope is not None:
        return False, f"判定为{scope.kind}案件，直接在进入分析前拦停，不调用大模型"
    return True, ""


def _load_checkpoint_payload(task_id: str) -> dict | None:
    """取回检查点里保存的整份 payload（视图 + 原始请求参数）。"""

    saved = StateStore(ROOT / "state").load(task_id)
    if not saved:
        return None
    payload = saved.get("payload") or {}
    return payload if isinstance(payload, dict) else None


def _run_task(
    task_id: str,
    description: str,
    evidence_names: list[str],
    role: str,
    expectation: float,
    confirmed_evidence: list[str] | None = None,
    waived_evidence: list[str] | None = None,
    quality_answers: dict[str, list[str]] | None = None,
    region: str = DEFAULT_REGION,
    approved: bool = False,
    request: dict | None = None,
    waive_all: bool = False,
) -> None:
    events: queue.Queue = TASKS[task_id]["events"]

    def on_event(event: Event) -> None:
        events.put(event)

    try:
        llm = None
        if os.environ.get("ANLI_DISABLE_LLM") != "1":
            llm = LLMClient()
            if not llm.available:
                llm = None
        # 开跑前先判断这次到底要不要调用大模型（超范围案件不浪费调用）
        configured = llm is not None
        use_llm, skip_reason = plan_intake(description)
        if not use_llm:
            llm = None
        orchestrator = _build_orchestrator(
            on_event, llm=llm, llm_configured=configured
        )
        profile = build_profile_llm(
            description,
            evidence_names,
            llm=llm,
            region=region or DEFAULT_REGION,
            confirmed_evidence=confirmed_evidence or [],
            waived_evidence=waived_evidence or [],
            quality_answers=quality_answers or {},
            waive_all=waive_all,
        )
        if skip_reason:
            profile.extra["intake_note"] = skip_reason
        else:
            # 「这次会调用大模型」在开跑瞬间就已经确定，直接告诉前端，不等第一次调用返回
            orchestrator.bus.emit(
                "llm_state",
                task_id,
                {"status": "calling", "text": "本次将调用大模型理解案情"},
            )
        if not expectation:
            expectation = profile.extra.get("expectation") or extract_expectation(description)
        record = orchestrator.run(
            task_id=task_id,
            role=Role(role),
            profile=profile,
            expectation=expectation,
            approved=approved,
        )
        # 把对话历史挂到结果上：前端要显示「我提交了什么」，检查点也要能恢复
        record.extra["rounds"] = [dict(item) for item in TASKS[task_id].get("rounds", [])]
        # 记录这次到底是「大模型版」「规则版」「提前结束没用模型」还是「配置了但没调通」
        calls = int(record.extra.get("llm_calls") or 0)
        failures = int(record.extra.get("llm_failures") or 0)
        status, status_text = llm_status_for(
            llm_configured=bool(record.extra.get("llm_configured", llm is not None)),
            calls=calls,
            failures=failures,
            last_failure=str(record.extra.get("llm_last_failure") or ""),
            stopped_early=bool(record.extra.get("out_of_scope"))
            or record.state == TaskState.NEEDS_HUMAN,
        )
        record.extra["llm_status"] = status
        record.extra["llm_status_text"] = status_text
        LAST_LLM_RUN.update(
            {
                "status": record.extra["llm_status"],
                "text": record.extra["llm_status_text"],
                "calls": calls,
                "failures": failures,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        TASKS[task_id]["record"] = record
        TASKS[task_id]["report"] = orchestrator.render_report(record)
        # 检查点存一份前端视图：服务重启后仍能取回这次任务的结果
        try:
            StateStore(ROOT / "state").save(
                task_id,
                record.state,
                {
                    "view": build_view(record, TASKS[task_id]["report"]),
                    "request": request or {},
                },
            )
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001 - 后台线程异常必须可见
        TASKS[task_id]["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        events.put(None)


def _start_task(
    task_id: str,
    description: str,
    evidence_names: list[str],
    role: str,
    expectation: float,
    confirmed_evidence: list[str] | None = None,
    waived_evidence: list[str] | None = None,
    quality_answers: dict[str, list[str]] | None = None,
    region: str = DEFAULT_REGION,
    approved: bool = False,
    waive_all: bool = False,
) -> None:
    """启动（或重启）一次分析，重置事件队列供前端重新订阅。"""

    TASKS[task_id]["events"] = queue.Queue()
    TASKS[task_id]["record"] = None
    TASKS[task_id]["report"] = ""
    request = {
        "description": description,
        "evidence_names": evidence_names,
        "role": role,
        "expectation": expectation,
        "region": region or DEFAULT_REGION,
        "approved": bool(approved),
        "waive_all": bool(waive_all),
        "confirmed_evidence": list(confirmed_evidence or []),
        "waived_evidence": list(waived_evidence or []),
        "quality_answers": {
            key: list(value) for key, value in (quality_answers or {}).items()
        },
    }
    TASKS[task_id]["request"] = request
    # 第 1 轮 = 初次提交；后续轮次由补交接口追加，避免重复计数
    rounds = TASKS[task_id].setdefault("rounds", [])
    if not rounds:
        rounds.append(
            {
                "round": 1,
                "message": description,
                "files": list(evidence_names),
                "confirmed": list(confirmed_evidence or []),
                "region": region or DEFAULT_REGION,
                "role": role,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
    threading.Thread(
        target=_run_task,
        args=(
            task_id,
            description,
            evidence_names,
            role,
            expectation,
            list(confirmed_evidence or []),
            list(waived_evidence or []),
            {key: list(value) for key, value in (quality_answers or {}).items()},
            region or DEFAULT_REGION,
            bool(approved),
            request,
            bool(waive_all),
        ),
        daemon=True,
    ).start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002 - 保持安静
        return

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send_text(render_index(), "text/html; charset=utf-8")
        elif path.startswith("/vendor/"):
            self._send_vendor(path[len("/vendor/") :])
        elif path == "/api/samples":
            self._json(
                {
                    "code": 0,
                    "samples": [
                        {
                            "key": key,
                            "description": description_factory(),
                            "files": files_factory(),
                        }
                        for key, (description_factory, files_factory) in SAMPLES.items()
                    ],
                }
            )
        elif path == "/api/health":
            self._json(health_payload())
        elif path == "/api/evidence-schema":
            self._json({"code": 0, "schema": QUALITY_QUESTIONS})
        elif path == "/api/regions":
            self._json({"code": 0, "default": DEFAULT_REGION, "regions": REGIONS})
        elif path.startswith("/api/tasks/") and path.endswith("/stream"):
            self._stream(path.split("/")[3])
        elif path.startswith("/api/tasks/") and path.endswith("/export"):
            self._export(path.split("/")[3])
        elif path.startswith("/api/tasks/"):
            self._task(path.split("/")[3])
        else:
            self._json({"code": 404, "msg": "未找到"}, status=404)

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/api/tasks/") and path.endswith("/evidence"):
            self._continue_with_evidence(path.split("/")[3])
            return
        if path.startswith("/api/tasks/") and path.endswith("/approve"):
            self._approve(path.split("/")[3])
            return
        if path != "/api/tasks":
            self._json({"code": 404, "msg": "未找到"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        description = (payload.get("description") or "").strip()
        file_names = payload.get("file_names") or []
        files = payload.get("files") or []
        role = payload.get("role", Role.CLAIMANT.value)
        expectation = float(payload.get("expectation") or 0)
        region = normalize_region((payload.get("region") or "")[:20])
        if role not in {item.value for item in Role}:
            self._json({"code": 400, "msg": "未知身份"}, status=400)
            return
        if not description:
            self._json({"code": 400, "msg": "请先描述你的纠纷"}, status=400)
            return

        task_id = uuid.uuid4().hex[:12]
        TASKS[task_id] = {"events": queue.Queue(), "record": None, "report": ""}
        saved = self._save_uploads(task_id, files)
        if saved.get("error"):
            self._json({"code": 400, "msg": saved["error"]}, status=400)
            return
        evidence_names = list(file_names) + list(saved.get("names") or [])
        confirmed = [str(name) for name in (payload.get("confirmed") or [])]
        waived = [str(name) for name in (payload.get("waived") or [])]
        quality = {
            str(name): [str(answer) for answer in (answers or [])]
            for name, answers in (payload.get("quality") or {}).items()
        }
        _start_task(
            task_id,
            description,
            evidence_names,
            role,
            expectation,
            confirmed,
            waived,
            quality,
            region,
        )
        self._json({"code": 0, "task_id": task_id})

    def _approve(self, task_id: str) -> None:
        """用户确认风险后继续：重跑一次，这次不再被转人工闸门拦住。"""

        task = TASKS.get(task_id)
        out_of_scope = False
        if task and "request" in task:
            record = task.get("record")
            out_of_scope = bool(record is not None and record.extra.get("out_of_scope"))
        else:
            # 服务重启后内存里没有任务：从检查点取回原始请求参数继续跑
            payload = _load_checkpoint_payload(task_id)
            request = (payload or {}).get("request")
            view = (payload or {}).get("view") or {}
            if not request:
                self._json({"code": 404, "msg": "任务不存在"}, status=404)
                return
            out_of_scope = bool(view.get("out_of_scope"))
            TASKS[task_id] = {
                "events": queue.Queue(),
                "record": None,
                "report": "",
                "request": request,
            }
            task = TASKS[task_id]
        if out_of_scope:
            self._json(
                {"code": 400, "msg": "该案情超出覆盖范围，不能继续分析"}, status=400
            )
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        request = dict(task["request"])
        request["approved"] = True
        note = (payload.get("note") or "").strip()
        if note:
            request["description"] = f"{request['description']}\n补充说明：{note}"
        _start_task(task_id, **request)
        self._json({"code": 0, "task_id": task_id})

    def _continue_with_evidence(self, task_id: str) -> None:
        """用户补交材料后，带着更完整的证据链重算一次。"""

        task = TASKS.get(task_id)
        if not task or "request" not in task:
            self._json({"code": 404, "msg": "任务不存在"}, status=404)
            return
        rounds = task.get("rounds") or []
        if len(rounds) >= MAX_ROUNDS:
            self._json(
                {
                    "code": 400,
                    "msg": f"已经补交 {len(rounds) - 1} 轮，达到上限（{MAX_ROUNDS - 1} 轮）。"
                    "如仍有新材料，请新建一次咨询。",
                },
                status=400,
            )
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        saved = self._save_uploads(task_id, payload.get("files") or [])
        if saved.get("error"):
            self._json({"code": 400, "msg": saved["error"]}, status=400)
            return
        request = dict(task["request"])
        request["evidence_names"] = list(request.get("evidence_names") or []) + list(
            saved.get("names") or []
        )
        confirmed = list(request.get("confirmed_evidence") or []) + [
            str(name) for name in (payload.get("confirmed") or [])
        ]
        request["confirmed_evidence"] = sorted(set(confirmed))
        merged_quality = {
            str(key): list(value)
            for key, value in (request.get("quality_answers") or {}).items()
        }
        for name, answers in (payload.get("quality") or {}).items():
            existing = set(merged_quality.get(str(name)) or [])
            merged_quality[str(name)] = sorted(
                existing | {str(item) for item in (answers or [])}
            )
        request["quality_answers"] = merged_quality
        if payload.get("waive_all"):
            # 用户明确表示拿不出材料：把上次列出的缺口全部标记为「无法提供」
            request["waive_all"] = True
            record = task.get("record")
            gaps: list[str] = []
            if record is not None:
                for item in record.extra.get("evidence_request") or []:
                    gaps.extend(str(name) for name in (item.get("missing") or []))
            request["waived_evidence"] = sorted(
                set(list(request.get("waived_evidence") or []) + gaps)
            )
        note = (payload.get("note") or payload.get("message") or "").strip()
        if note:
            request["description"] = f"{request['description']}\n补充说明：{note}"
        rounds.append(
            {
                "round": len(rounds) + 1,
                "message": note,
                "files": list(saved.get("names") or []),
                "confirmed": [str(name) for name in (payload.get("confirmed") or [])],
                "waived": bool(payload.get("waive_all")),
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        task["rounds"] = rounds
        _start_task(task_id, **request)
        self._json({"code": 0, "task_id": task_id})

    def _save_uploads(self, task_id: str, files: list) -> dict:
        """把用户上传的证据落盘，返回文件名列表。"""

        if not files:
            return {}
        upload_dir = ROOT / "cases" / task_id / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        names: list[str] = []
        total = 0
        for item in files:
            name = str(item.get("name") or "材料")
            data = item.get("data") or ""
            try:
                raw = base64.b64decode(data, validate=False)
            except Exception:  # noqa: BLE001
                return {"error": f"材料 {name} 解析失败"}
            total += len(raw)
            if total > MAX_UPLOAD_BYTES:
                return {"error": "上传材料总体积超过 8MB 限制"}
            safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|') or "材料"
            (upload_dir / safe).write_bytes(raw)
            names.append(name)
        return {"names": names}

    # ------------------------------------------------------------------ 内部
    def _stream(self, task_id: str) -> None:
        task = TASKS.get(task_id)
        if not task:
            self._json({"code": 404, "msg": "任务不存在"}, status=404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        events: queue.Queue = task["events"]
        while True:
            event = events.get()
            if event is None:
                break
            chunk = (
                f"event: {event.type}\n"
                f"data: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
            )
            try:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def _export(self, task_id: str) -> None:
        """导出整份分析报告（默认 Word，可带 ?format=md 导出 Markdown）。

        服务重启后进程内没有记录：退回检查点里的前端视图重建文档，
        用户不需要因为"重启过"而重新跑一遍。
        """

        fmt = parse_qs(urlparse(self.path).query).get("format", ["docx"])[0].lower()
        task = TASKS.get(task_id)
        record = (task or {}).get("record")
        if record is not None:
            model = export_model_from_record(record, task_id)
        else:
            view = _load_checkpoint(task_id)
            if view is None:
                self._json(
                    {"code": 404, "msg": "任务不存在或尚未完成，请刷新页面后重试"},
                    status=404,
                )
                return
            model = export_model_from_view(view, task_id)

        if fmt == "md":
            body = render_markdown(model).encode("utf-8")
            content_type = "text/markdown; charset=utf-8"
            extension = "md"
        else:
            body = build_docx(model)
            content_type = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            extension = "docx"
        filename = _urlquote(f"案理法律分析报告-{task_id}.{extension}")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=report-{task_id}.{extension}; filename*=UTF-8''{filename}",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _task(self, task_id: str) -> None:
        if not TASK_ID_PATTERN.match(task_id or ""):
            self._json({"code": 400, "msg": "非法任务号"}, status=400)
            return
        task = TASKS.get(task_id)
        if not task:
            # 进程内没有：尝试从检查点恢复（服务重启后仍能取回上次的结果）
            restored = _load_checkpoint(task_id)
            if restored is None:
                self._json({"code": 404, "msg": "任务不存在"}, status=404)
                return
            restored["restored"] = True
            self._json(restored)
            return
        record = task["record"]
        if record is None:
            self._json(
                {
                    "code": 0,
                    "state": "RUNNING",
                    "error": task.get("error", ""),
                    "restored": False,
                }
            )
            return
        view = build_view(record, task.get("report", ""), task.get("error", ""))
        view["restored"] = False
        self._json(view)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self._json({"code": 404, "msg": "文件不存在"}, status=404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if content_type.startswith("text/html"):
            # 界面改动频繁，禁止浏览器缓存旧页面
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_vendor(self, relative: str) -> None:
        """只允许读取 web/vendor 目录下的静态资源。"""

        base = (WEB / "vendor").resolve()
        target = (base / relative).resolve()
        if base != target and base not in target.parents:
            self._json({"code": 403, "msg": "非法路径"}, status=403)
            return
        suffix = target.suffix.lower()
        content_type = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".woff2": "font/woff2",
        }.get(suffix, "application/octet-stream")
        self._send_file(target, content_type)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """启动服务；端口被占用时自动顺延，并打开浏览器。"""

    port = int(os.environ.get("ANLI_PORT", port))
    if os.environ.get("ANLI_NO_BROWSER") == "1":
        open_browser = False

    server = None
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer((host, candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        print("端口被占用，未能启动服务。请关闭其他案理窗口后重试。")
        return

    url = f"http://{host}:{port}"
    print(f"案理服务已启动：{url}")
    print(f"页面与数据目录：{user_root()}")
    print("保持本窗口开着；关闭窗口即停止服务。")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("已停止服务。")


if __name__ == "__main__":  # pragma: no cover
    main()
