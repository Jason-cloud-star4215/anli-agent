"""一次运行、一次留痕：跑主线案例并导出全部证据文件。

为什么要这个脚本：提交材料里的「过程日志」「导出报告」「模型调用环节表」必须来自
**同一次运行**。手工分几次导出很容易让正文与附件出现两个不同的数字（例如正文写
48.9 秒、附件里还躺着上一次的 40.7 秒）。本脚本把这一次运行的所有产物一次性落盘，
并在最后做一次口径核对，把「调用次数 / 耗时 / token」是否处处一致直接打在屏幕上。

用法：
    # 1) 先启动服务（源码或打包版都行，二选一）
    python -m anli.web            # 源码运行，默认 http://127.0.0.1:8765
    dist\\案理.exe                 # 打包版，同样是 8765

    # 2) 采集（默认写入 artifacts\\，并覆盖同名文件）
    python tools/capture_llm_log.py --source "dist\\案理.exe（打包版）"

产出：
    过程日志_LLM链路.txt      SSE 原样导出，带相对秒级时间戳
    导出报告_LLM链路.md       交付物报告（服务端 /export?format=md）
    金额认定表_LLM链路.csv    金额认定表（服务端 /export?format=csv）
    模型调用明细_LLM链路.md   逐次调用的环节 / 耗时 / token + 口径核对
    工具调用清单_LLM链路.md   检索、官方库回源核对、引用核验的调用清单

主线案例固定在脚本里（与前端 ?demo=car 的「主张方」示例逐字一致），保证可复现。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# 与 web/index.src.html 的 SAMPLES.car.claimant 逐字一致（含证据勾选与期望金额）。
# 末尾一句把"手上已有哪些材料"写死：否则"哪些材料已在手"由模型抽取，重复运行会出现
# 不同证据充分度（观察过 62%／80%／86%），展示与截图就不稳定。
DEMO_DESCRIPTION = (
    "6月10号我骑电动车被一辆小轿车撞了，交警出了事故认定书，对方主要责任。"
    "门诊治疗花了医疗费3200元，因为养伤误工费大概1800元，请护工花了护理费900元，"
    "打车去医院的交通费300元，医生说要补充营养，营养费400元。我想要对方一共赔我12000元。"
    "现在我手上只有事故认定书、医疗费票据和误工证明这三样，护理费、交通费、营养费的票据都还没找到，"
    "工资流水也还没打。"
)
DEMO_EVIDENCE = ["事故认定书", "医疗费票据", "误工证明"]
DEMO_EXPECTATION = 12000
DEMO_REGION = "浙江省"
DEMO_ROLE = "claimant"


def post_json(base: str, path: str, payload: dict, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_bytes(base: str, path: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(base + path, timeout=timeout) as response:
        return response.read()


def get_json(base: str, path: str, timeout: int = 60) -> dict:
    return json.loads(get_bytes(base, path, timeout).decode("utf-8"))


def stream_events(base: str, task_id: str, timeout: int) -> list[dict]:
    """读取 SSE 事件流，返回 [{type, payload, t}]，t 为相对任务发起的秒数。"""

    events: list[dict] = []
    started = time.monotonic()
    request = urllib.request.Request(base + f"/api/tasks/{task_id}/stream")
    pending_type: str | None = None
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("event: "):
                pending_type = line[len("event: "):]
            elif line.startswith("data: ") and pending_type:
                payload = json.loads(line[len("data: "):])
                events.append(
                    {
                        "type": pending_type,
                        "payload": payload,
                        "t": time.monotonic() - started,
                    }
                )
                if pending_type in {"task_completed", "task_failed"}:
                    break
                pending_type = None
            elif not line:
                pending_type = None
    return events


def render_log_header(
    source: str, model: str, task_id: str, created: str, calls: int, seconds: float, decisions: int
) -> list[str]:
    return [
        f"# 案理 · 大模型真实链路过程日志（SSE 原样导出，{created[:10]}）",
        f"# 环境：{source} → DeepSeek {model}，{calls} 次调用，全程 {seconds:.1f} 秒，"
        f"{decisions} 条规划决策留痕",
        "# " + "-" * 70,
        f"# 任务 {task_id} 创建于 {created}",
        f"# 案情：{DEMO_DESCRIPTION}",
        "# " + "-" * 70,
    ]


def render_event_lines(events: list[dict]) -> list[str]:
    """还原现有日志的两行格式：[  x.xs] event: <type> / [  x.xs] : <text> <json>。"""

    lines: list[str] = []
    for item in events:
        payload = dict(item["payload"])
        text = payload.pop("text", None)
        stamp = f"[{item['t']:7.1f}s]"
        lines.append(f"{stamp} event: {item['type']}")
        body = json.dumps(payload, ensure_ascii=False)
        lines.append(f"{stamp} : " + ((text + " ") if text else " ") + body)
    return lines


def render_call_table(view: dict, event_count: int) -> list[str]:
    log = view.get("llm_call_log") or []
    cost = view.get("cost") or {}
    calls = int(view.get("llm_calls") or 0)
    prompt = sum(int(item.get("prompt_tokens") or 0) for item in log)
    completion = sum(int(item.get("completion_tokens") or 0) for item in log)
    seconds = sum(float(item.get("seconds") or 0) for item in log)

    retries = [item for item in log if int(item.get("attempt") or 1) > 1]
    lines = [
        "# 模型调用明细（同一次运行的逐次留痕）",
        "",
        f"- 调用次数：view.llm_calls = {calls}；成本行 cost.calls = {cost.get('calls')}；"
        f"明细行数 = {len(log)}",
        f"- 耗时：成本行 cost.seconds = {cost.get('seconds')} 秒；明细合计 = {seconds:.1f} 秒",
        f"- token：成本行 prompt/completion = {cost.get('prompt_tokens')}/{cost.get('completion_tokens')}；"
        f"明细合计 = {prompt}/{completion}",
        f"- 过程流事件数：{event_count}；其中 JSON 校验失败后的重试调用：{len(retries)} 次",
        "",
        "> 说明：同一环节出现多行，表示该环节实际调用了多次（模型返回的 JSON 未通过校验时会自动重试，"
        "记为第 2／3 次尝试）。本表按**实际发出的每一次 API 调用**逐条记录，不做合并。",
        "",
        "| # | 环节 | 第几次尝试 | 耗时（秒） | 输入 token | 输出 token |",
        "|---|---|---|---|---|---|",
    ]
    for item in log:
        attempt = int(item.get("attempt") or 1)
        mark = f"第 {attempt} 次" + ("（重试）" if attempt > 1 else "")
        lines.append(
            f"| {item.get('index')} | {item.get('purpose')} | {mark} | {item.get('seconds')} | "
            f"{item.get('prompt_tokens')} | {item.get('completion_tokens')} |"
        )
    if log:
        # 合计行取自报告成本行（权威值）；逐行是各自四舍五入后的值，
        # 行合计与此可能有 ±0.1 秒的取整差异，表下已注明
        lines.append(
            f"| — | 合计 | — | {float(cost.get('seconds') or 0.0):.1f} | "
            f"{cost.get('prompt_tokens')} | {cost.get('completion_tokens')} |"
        )
    lines.append("")
    lines.append(
        "> 合计行取自报告成本行（`cost.seconds` / `cost.*_tokens`）；逐行耗时各自四舍五入到 0.01 秒，"
        "行合计与合计行可能存在 ±0.1 秒的取整差异，属正常现象。"
    )
    lines.append("")
    return lines


def render_tool_table(events: list[dict]) -> list[str]:
    queries = [e for e in events if e["type"] == "retrieval_query"]
    hits = [e for e in events if e["type"] == "retrieval_hit"]
    official = [e for e in hits if e["payload"].get("scope") == "law_web"]
    citations = [e for e in events if e["type"] == "citation_verified"]
    lines = [
        "# 工具调用清单（同一次运行的原始事件）",
        "",
        f"- 检索查询：{len(queries)} 次；命中：{len(hits)} 条",
        f"- 官方库回源核对：{len(official)} 部法律；引用核验：{len(citations)} 条",
        "",
        "| # | 时刻 | 工具 | 入参（查询） | 出参（命中） |",
        "|---|---|---|---|---|",
    ]
    tool_names = {
        "law": "法条检索（内置法条库，23 条）",
        "case": "类案检索（内置类案库）",
        "law_web": "法规原文回源核对（国家法律法规数据库）",
    }
    scopes: list[str] = []
    for item in events:
        if item["type"] != "retrieval_query":
            continue
        scope = str(item["payload"].get("scope") or "")
        if scope not in scopes:
            scopes.append(scope)

    index = 0
    for scope in scopes:
        queries = [e for e in events if e["type"] == "retrieval_query" and e["payload"].get("scope") == scope]
        hits = [e for e in events if e["type"] == "retrieval_hit" and e["payload"].get("scope") == scope]
        if not queries:
            continue
        index += 1
        in_text = "；".join(
            f"{q['payload'].get('query')}（{q['payload'].get('detail')}）" for q in queries
        )
        labels = [str(h["payload"].get("label")) for h in hits]
        if labels:
            out_text = f"命中 {len(labels)} 条：" + "；".join(labels)
        else:
            out_text = "（无命中）"
        lines.append(
            f"| {index} | {queries[0]['t']:.1f}s | {tool_names.get(scope, scope)} | "
            f"{in_text} | {out_text} |"
        )

    lines += ["", "## 官方库回源核对与引用核验", ""]
    lines.append("| # | 时刻 | 类型 | 内容 | 结论 |")
    lines.append("|---|---|---|---|---|")
    index = 0
    for item in events:
        payload = item["payload"]
        if item["type"] == "retrieval_hit" and payload.get("scope") == "law_web":
            index += 1
            lines.append(
                f"| {index} | {item['t']:.1f}s | 官方库收录 | {payload.get('label')} | "
                f"{payload.get('detail')} |"
            )
        elif item["type"] == "citation_verified":
            index += 1
            grade = payload.get("grade")
            note = payload.get("note") or ""
            if grade == "synthetic":
                note = "演示数据，不作为法律依据"
            lines.append(
                f"| {index} | {item['t']:.1f}s | 引用核验 | {payload.get('label')} | "
                f"verified={payload.get('verified')}（{grade}）{note} |"
            )
    lines.append("")
    return lines


def parse_log(text: str) -> list[dict]:
    """从过程日志反向解析出事件（用于不重新调用模型就重建表格）。"""

    events: list[dict] = []
    pending: dict | None = None
    for line in text.splitlines():
        if line.startswith("#") or not line.startswith("["):
            continue
        stamp = line[: line.index("]") + 1]
        try:
            seconds = float(stamp.strip("[]s "))
        except ValueError:
            continue
        if " event: " in line:
            pending = {"type": line.split(" event: ", 1)[1].strip(), "t": seconds}
            continue
        if pending is None:
            continue
        body = line.split(": ", 1)[1] if ": " in line else ""
        brace = body.find("{")
        if brace < 0:
            continue
        pending["payload"] = json.loads(body[brace:])
        events.append(pending)
        pending = None
    return events


def rebuild_from_log(log_path: Path, state_path: Path, out_dir: Path) -> int:
    """不重新调用模型：从已有过程日志 + 状态文件重建两张表（数字不漂移）。"""

    events = parse_log(log_path.read_text(encoding="utf-8"))
    view = json.loads(state_path.read_text(encoding="utf-8"))["payload"]["view"]
    (out_dir / "模型调用明细_LLM链路.md").write_text(
        "\n".join(render_call_table(view, len(events))), encoding="utf-8"
    )
    (out_dir / "工具调用清单_LLM链路.md").write_text(
        "\n".join(render_tool_table(events)), encoding="utf-8"
    )
    print(f"已从 {log_path.name} 重建两张表：事件数 {len(events)}，明细行数 {len(view.get('llm_call_log') or [])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(description="跑主线案例并导出全部证据文件")
    parser.add_argument("--base", default="http://127.0.0.1:8765", help="服务地址")
    parser.add_argument("--out-dir", default=str(ROOT / "artifacts"), help="产物目录")
    parser.add_argument("--source", default="dist\\案理.exe（打包版）", help="写进日志表头的运行环境说明")
    parser.add_argument("--model", default="deepseek-chat", help="写进日志表头的模型名")
    parser.add_argument("--timeout", type=int, default=600, help="SSE 读取超时（秒）")
    parser.add_argument(
        "--rebuild-from-log",
        default=None,
        help="不调用模型：从已有过程日志重建两张表（需同时给 --state）",
    )
    parser.add_argument("--state", default=None, help="state/<task>.json，供重建模式读取权威数字")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = args.base.rstrip("/")

    if args.rebuild_from_log:
        if not args.state:
            print("重建模式需要 --state 指向 state/<task>.json")
            return 2
        return rebuild_from_log(Path(args.rebuild_from_log), Path(args.state), out_dir)

    print(f"[1/5] 发起主线案例：{base}")
    created = time.strftime("%Y-%m-%d %H:%M:%S")
    response = post_json(
        base,
        "/api/tasks",
        {
            "description": DEMO_DESCRIPTION,
            "role": DEMO_ROLE,
            "expectation": DEMO_EXPECTATION,
            "region": DEMO_REGION,
            "file_names": [],
            "files": [],
            "confirmed": DEMO_EVIDENCE,
        },
    )
    task_id = response.get("task_id")
    if not task_id:
        print(f"发起失败：{response}")
        return 1
    print(f"      task_id = {task_id}")

    print("[2/5] 接收 SSE 事件流…")
    events = stream_events(base, task_id, args.timeout)
    kinds = [item["type"] for item in events]
    if "task_failed" in kinds:
        failure = next(item for item in events if item["type"] == "task_failed")
        print(f"      任务失败：{failure['payload']}")
        return 1
    if "task_completed" not in kinds:
        print("      未收到 task_completed，事件流被中断。")
        return 1
    print(f"      事件数：{len(events)}")

    print("[3/5] 取回任务视图与导出件…")
    view = get_json(base, f"/api/tasks/{task_id}")
    report_md = get_bytes(base, f"/api/tasks/{task_id}/export?format=md")
    amount_csv = get_bytes(base, f"/api/tasks/{task_id}/export?format=csv")
    cost = view.get("cost") or {}
    decisions = view.get("planning_decisions") or []

    header = render_log_header(
        args.source,
        args.model,
        task_id,
        created,
        int(cost.get("calls") or 0),
        float(cost.get("seconds") or 0.0),
        len(decisions),
    )
    (out_dir / "过程日志_LLM链路.txt").write_text(
        "\n".join(header + render_event_lines(events)) + "\n", encoding="utf-8"
    )
    (out_dir / "导出报告_LLM链路.md").write_bytes(report_md)
    (out_dir / "金额认定表_LLM链路.csv").write_bytes(amount_csv)
    (out_dir / "模型调用明细_LLM链路.md").write_text(
        "\n".join(render_call_table(view, len(events))), encoding="utf-8"
    )
    (out_dir / "工具调用清单_LLM链路.md").write_text(
        "\n".join(render_tool_table(events)), encoding="utf-8"
    )
    # 事件 JSON 旁路文件：以后要重建表格或改格式，不必再调用一次模型
    (out_dir / "过程日志_LLM链路.events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("[4/5] 口径核对")
    calls = int(view.get("llm_calls") or 0)
    log = view.get("llm_call_log") or []
    checks = [
        ("调用次数（状态行 / 成本行 / 明细）", calls, int(cost.get("calls") or 0), len(log)),
        (
            "耗时（成本行 / 明细合计）",
            float(cost.get("seconds") or 0.0),
            round(sum(float(i.get("seconds") or 0) for i in log), 1),
        ),
        (
            "输入 token（成本行 / 明细合计）",
            int(cost.get("prompt_tokens") or 0),
            sum(int(i.get("prompt_tokens") or 0) for i in log),
        ),
        (
            "输出 token（成本行 / 明细合计）",
            int(cost.get("completion_tokens") or 0),
            sum(int(i.get("completion_tokens") or 0) for i in log),
        ),
    ]
    all_ok = True
    for name, *values in checks:
        if name.startswith("耗时"):
            # 逐行耗时各自四舍五入，行合计与成本行允许 ±0.2 秒的取整差异
            ok = abs(float(values[0]) - float(values[1])) <= 0.2
        else:
            ok = len({round(float(v), 1) for v in values}) == 1
        all_ok = all_ok and ok
        joined = " / ".join(str(v) for v in values)
        print(f"      {'✔' if ok else '✘'} {name}：{joined}")
    print(f"      交付物章节：{len([line for line in view.get('report', '').splitlines() if line.startswith('=====')])} 项；"
          f"引用核验：{len(view.get('citations') or [])} 条；"
          f"规划决策：{len(decisions)} 条；状态：{view.get('state')}")
    if not all_ok:
        print("      ⚠ 口径不一致，先不要用这一轮的数字改文档。")
        return 2

    print("[5/5] 产物已写入：")
    for name in (
        "过程日志_LLM链路.txt",
        "导出报告_LLM链路.md",
        "金额认定表_LLM链路.csv",
        "模型调用明细_LLM链路.md",
        "工具调用清单_LLM链路.md",
    ):
        print(f"      {out_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
