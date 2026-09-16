"""命令行入口：先跑通主线案例，再考虑接前端。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .events import Event
from .intake import build_profile, extract_expectation
from .llm import LLMClient
from .llm_intake import build_profile_llm
from .paths import resource_path, user_root
from .models import Role
from .orchestrator import Orchestrator
from .regions import DEFAULT_REGION, normalize_region
from .retrieval_flk import LawSourceChecker
from .scenarios import (
    car_accident_expectation,
    car_accident_profile,
    consumption_expectation,
    consumption_profile,
)
from .state import StateStore
from .stores import CaseStore, LawStore

ROOT = user_root()
DATA = resource_path("data")


def build_orchestrator(
    verbose: bool = False, use_llm: bool = True, use_web: bool = True
) -> Orchestrator:
    law_store = LawStore.load(DATA / "laws.seed.json")
    case_store = CaseStore.load(DATA / "cases.seed.json", DATA / "cases.official.json")
    llm = LLMClient() if use_llm else None
    if llm is not None and not llm.available:
        llm = None
    checker = (
        LawSourceChecker(cache_path=ROOT / "cache" / "law_sources.json")
        if use_web
        else None
    )
    orchestrator = Orchestrator(
        law_store=law_store,
        case_store=case_store,
        state_store=StateStore(ROOT / "state"),
        llm=llm,
        law_checker=checker,
    )
    if verbose:
        orchestrator.subscribe(_print_event)
    return orchestrator


def _print_event(event: Event) -> None:
    payload = json.dumps(event.payload, ensure_ascii=False)
    print(f"[{event.sequence:02d}] {event.type} {payload}")


def cmd_demo(args: argparse.Namespace) -> int:
    scenario = args.scenario
    region = normalize_region(args.region)
    if scenario == "consumption":
        profile = consumption_profile(region=region)
        expectation = consumption_expectation()
    else:
        profile = car_accident_profile(region=region)
        expectation = car_accident_expectation()

    orchestrator = build_orchestrator(
        verbose=args.verbose, use_llm=not args.no_llm, use_web=not args.no_web
    )
    record = orchestrator.run(
        task_id=f"demo-{scenario}",
        role=Role(args.role),
        profile=profile,
        expectation=expectation,
    )

    if args.json:
        text = json.dumps(asdict(record), ensure_ascii=False, indent=2, default=str)
        _write_out(args.out, text)
        print(text)
        return 0 if record.state.value == "DELIVERED" else 1

    report = "\n".join(
        [
            f"任务状态：{record.state.value}",
            f"用户身份：{record.role.value}",
            f"复核意见：{len(record.findings)} 条",
            "",
            orchestrator.render_report(record),
        ]
    )
    _write_out(args.out, report)
    print(report)
    return 0 if record.state.value in {"DELIVERED", "NEEDS_EVIDENCE", "NEEDS_INFO"} else 1


def cmd_ask(args: argparse.Namespace) -> int:
    """用一段真实的案情描述跑一遍完整流程。"""

    if not args.desc:
        print("请用 --desc 提供案情描述。")
        return 2
    evidence_names = list(args.evidence or [])
    llm = LLMClient() if not args.no_llm else None
    if llm is not None and not llm.available:
        llm = None
    profile = build_profile_llm(
        args.desc, evidence_names, llm=llm, region=normalize_region(args.region)
    )
    expectation = args.expect or profile.extra.get("expectation") or extract_expectation(args.desc)
    if not expectation:
        print("未能从描述中识别期望金额，请用 --expect 指定。")
        return 2

    orchestrator = build_orchestrator(
        verbose=args.verbose, use_llm=not args.no_llm, use_web=not args.no_web
    )
    record = orchestrator.run(
        task_id=f"ask-{abs(hash(args.desc)) % 100000}",
        role=Role(args.role),
        profile=profile,
        expectation=expectation,
    )
    report = orchestrator.render_report(record)
    _write_out(args.out, report)
    print(report)
    return 0 if record.state.value in {"DELIVERED", "NEEDS_EVIDENCE", "NEEDS_INFO"} else 1


def _write_out(path: str | None, text: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"[已导出] {target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anli", description="案理 · 最小可用骨架")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="运行主线演示案例")
    demo.add_argument("--scenario", choices=["car", "consumption"], default="car")
    demo.add_argument(
        "--role",
        choices=[role.value for role in Role],
        default=Role.CLAIMANT.value,
    )
    demo.add_argument("--json", action="store_true", help="输出机器可读结果")
    demo.add_argument("--verbose", action="store_true", help="打印事件流")
    demo.add_argument("--out", default=None, help="把结果导出到指定文件")
    demo.add_argument("--no-llm", action="store_true", help="只用规则版，不调用大模型")
    demo.add_argument("--no-web", action="store_true", help="跳过官方库核对（离线演示）")
    demo.add_argument("--region", default=DEFAULT_REGION, help="适用省份，例如 浙江省")
    demo.set_defaults(func=cmd_demo)

    ask = sub.add_parser("ask", help="按真实案情描述跑一遍流程")
    ask.add_argument("--desc", required=True, help="用你自己的话描述纠纷")
    ask.add_argument("--evidence", action="append", default=[], help="已有证据的名称，可重复")
    ask.add_argument("--expect", type=float, default=0, help="你的期望金额")
    ask.add_argument("--region", default="", help="所在地区")
    ask.add_argument("--no-web", action="store_true", help="跳过官方库核对（离线演示）")
    ask.add_argument(
        "--role",
        choices=[role.value for role in Role],
        default=Role.CLAIMANT.value,
    )
    ask.add_argument("--out", default=None, help="导出到指定文件")
    ask.add_argument("--verbose", action="store_true", help="打印事件流")
    ask.add_argument("--no-llm", action="store_true", help="只用规则版，不调用大模型")
    ask.set_defaults(func=cmd_ask)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
