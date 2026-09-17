"""封版核对：一条命令看清「代码 / 数据 / 素材 / 打包产物」是否互相对得上。

为什么需要它：提交材料（初赛方案、截图清单、验收证据）里的数字是手写的，
代码或演示数据一改，这些数字就会过期，而且过期了没人会发现——上次打包版 exe
停在两天前的代码上，就是这么来的。

用法：
    python tools/seal_check.py

输出三类结果：
    [OK]   一致
    [改]   需要跟着改（例如测试数变了，材料里的数字要更新）
    [重跑] 产物落后于代码，需要重新生成
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"

REPORT_MATERIALS = [
    ARTIFACTS / "验收证据.txt",
    ARTIFACTS / "截图清单.md",
    ARTIFACTS / "过程日志_LLM链路.txt",
    ARTIFACTS / "导出报告_LLM链路.md",
]

results: list[tuple[str, str]] = []


def record(tag: str, message: str) -> None:
    results.append((tag, message))
    print(f"[{tag}] {message}" if tag == "OK" else f"[{tag}]{' ' * (4 - len(tag))}{message}")


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return proc.stdout.strip()


def load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    print("=" * 68)
    print("案理 · 封版核对")
    print("=" * 68)

    # ---------------------------------------------------------------- 代码状态
    head = git("log", "-1", "--format=%h %cI %s")
    dirty = git("status", "--porcelain")
    # 产物只看「代码与数据」的最近一次改动：纯文档提交不会让 exe 或素材作废
    code_epoch = float(
        git("log", "-1", "--format=%ct", "--", "anli", "web", "data", "app.py", "案理.spec") or 0
    )
    code_commit = git("log", "-1", "--format=%h %cI %s", "--", "anli", "web", "data", "app.py")
    record("OK", f"代码：{head}")
    record("OK", f"最近一次代码/数据改动：{code_commit}")
    if dirty:
        record("改", f"工作树有未提交改动 {len(dirty.splitlines())} 处，材料里的数字可能还没对齐")
    else:
        record("OK", "工作树干净")

    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip().splitlines()
    test_count = ""
    for line in reversed(tests):
        if "test" in line and ("collected" in line or "selected" in line):
            test_count = line.split()[0]
            break
    record("OK", f"测试：{test_count} 项（材料里写的必须与这个数一致）")

    # ---------------------------------------------------------------- 数据
    laws = load(DATA / "laws.seed.json", [])
    verified = sum(1 for item in laws if item.get("verified"))
    official = load(DATA / "cases.official.json", [])
    seed = load(DATA / "cases.seed.json", [])
    empirical = load(DATA / "real_cases.json", [])
    record("OK", f"法条库：{len(laws)} 条，其中已核验 {verified} 条")
    record("OK", f"官方案例：{len(official)} 条；演示样本：{len(seed)} 条；实证库：{len(empirical)} 条")

    # ---------------------------------------------------------------- 演示案例
    from anli.llm import LLMClient
    from anli.models import Role
    from anli.orchestrator import Orchestrator
    from anli.scenarios import (
        car_accident_expectation,
        car_accident_profile,
        consumption_expectation,
        consumption_profile,
    )
    from anli.state import StateStore
    from anli.stores import CaseStore, LawStore

    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(
            DATA / "cases.seed.json", DATA / "cases.official.json", DATA / "real_cases.json"
        ),
        state_store=StateStore(),
        llm=LLMClient(api_key=""),  # 只跑规则版，不消耗模型额度
    )
    for name, profile, expectation in (
        ("?demo=car", car_accident_profile, car_accident_expectation),
        ("?demo=consumption", consumption_profile, consumption_expectation),
    ):
        record_obj = orchestrator.run(
            task_id=f"seal-{name.strip('?demo=')}",
            role=Role.CLAIMANT,
            profile=profile(),
            expectation=expectation(),
        )
        a = record_obj.assessment
        band = (
            f"{a.case_low:,.0f}–{a.case_high:,.0f} 元"
            if a.case_low is not None
            else "暂不提供（量级不匹配）"
        )
        record(
            "OK",
            f"{name}：可支持 {a.legal_cap:,.0f} 元，区间 {band}，"
            f"样本 {a.sample_size} 件（演示 {a.synthetic_sample_size} 件），"
            f"交付物 {len(record_obj.artifacts)} 项",
        )

    # ---------------------------------------------------------------- 产物新鲜度
    exe = ROOT / "dist" / "案理.exe"
    if not exe.exists():
        record("重跑", "dist/案理.exe 不存在：python -m PyInstaller 案理.spec --noconfirm")
    elif exe.stat().st_mtime < code_epoch:
        built = datetime.fromtimestamp(exe.stat().st_mtime)
        record(
            "重跑",
            f"dist/案理.exe 构建于 {built:%Y-%m-%d %H:%M}，早于最近一次代码/数据改动——"
            "需要重新打包：python -m PyInstaller 案理.spec --noconfirm",
        )
    else:
        record("OK", "dist/案理.exe 不早于最近一次代码/数据改动")

    src = ROOT / "web" / "index.src.html"
    built_html = ROOT / "web" / "index.html"
    if src.exists() and built_html.exists() and built_html.stat().st_mtime < src.stat().st_mtime:
        record("重跑", "web/index.html 落后于 index.src.html：python tools/build_web.py")
    else:
        record("OK", "web/index.html 与源文件同步")

    for material in REPORT_MATERIALS:
        if not material.exists():
            record("重跑", f"缺素材：{material.relative_to(ROOT)}")
            continue
        mtime = datetime.fromtimestamp(material.stat().st_mtime)
        code_dt = datetime.fromtimestamp(code_epoch)
        if mtime < code_dt:
            record(
                "重跑",
                f"{material.name} 生成于 {mtime:%m-%d %H:%M}，早于最近一次代码/数据改动 "
                f"{code_dt:%m-%d %H:%M}——重新生成后再同步到提交材料目录",
            )
        else:
            record("OK", f"{material.name} 不早于最近一次代码/数据改动")

    # ---------------------------------------------------------------- 汇总
    print("-" * 68)
    to_fix = [message for tag, message in results if tag != "OK"]
    if not to_fix:
        print("全部一致：材料里的数字可以直接用。")
        return 0
    print("需要处理：")
    for index, message in enumerate(to_fix, start=1):
        print(f"  {index}. {message}")
    print()
    print("提醒：材料目录有两份（仓库 artifacts/ 与桌面上《AI杭州：模拟法庭》里的副本），改完要一起同步。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
