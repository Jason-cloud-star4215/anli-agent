"""把 artifacts 里的冻结口径自动同步到全部材料。

用法：python tools/sync_frozen_numbers.py
从 artifacts/过程日志_LLM链路.txt 与 artifacts/导出报告_LLM链路.md 读取
（构建时间 / 调用次数 / 耗时 / run 编号 / tokens），再把材料里出现过的
旧值替换为新值（支持多次运行迭代，旧值从材料里现场探测）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = Path(r"C:\Users\wangzheng\Desktop\ai杭州模拟法庭")

MATERIALS = [
    PKG / "00_提交自检报告.md",
    PKG / "ZCode_全量知识转储.md",
    PKG / "01_初赛方案" / "初赛方案_全文.md",
    PKG / "02_演示素材" / "截图清单.md",
    PKG / "02_演示素材" / "DEMO_SCRIPT.md",
    PKG / "04_附录" / "封版检查清单.md",
    PKG / "05_程序包" / "使用说明.md",
    REPO / "ZCode_全量知识转储.md",
    REPO / "DEMO_SCRIPT.md",
    REPO / "封版检查清单.md",
    REPO / "artifacts" / "截图清单.md",
]

log_head = (REPO / "artifacts" / "过程日志_LLM链路.txt").read_text(encoding="utf-8").splitlines()[1]
report = (REPO / "artifacts" / "导出报告_LLM链路.md").read_text(encoding="utf-8")
detail = (REPO / "artifacts" / "模型调用明细_LLM链路.md").read_text(encoding="utf-8")

build_time = re.search(r"(\d{2}:\d{2}) 构建", log_head).group(1)
calls = re.search(r"(\d+) 次调用", log_head).group(1)
seconds = re.search(r"全程 ([\d.]+) 秒", log_head).group(1)
run_id = re.search(r"报告编号 \| ([0-9a-f]{12})", report).group(1)
tokens = re.search(r"prompt/completion = ([\d,]+)/([\d,]+)", detail)
in_tok, out_tok = (tokens.group(1), tokens.group(2)) if tokens else ("", "")

print(f"目标口径：build={build_time} calls={calls} seconds={seconds} run={run_id} tokens={in_tok}+{out_tok}")

# 现场探测材料里的旧值（上一轮的 run / calls / seconds / build 时间）
old_run = old_calls = old_seconds = old_build = None
for f in MATERIALS:
    if not f.exists():
        continue
    t = f.read_text(encoding="utf-8")
    m = re.search(r"run `([0-9a-f]{12})`|运行 ([0-9a-f]{12})|报告编号 \| ([0-9a-f]{12})", t)
    if m and not old_run:
        old_run = next(g for g in m.groups() if g)
    m = re.search(r"模型参与 (\d+) 次|(\d+) 次调用", t)
    if m and not old_calls:
        old_calls = next(g for g in m.groups() if g)
    m = re.search(r"全程约 (\d+) 秒|全程 ([\d.]+) 秒|、\*\*([\d.]+) 秒\*\*", t)
    if m and not old_seconds:
        old_seconds = next(g for g in m.groups() if g)
    m = re.search(r"（2026-09-18 (\d{2}:\d{2})", t)
    if m and not old_build:
        old_build = m.group(1)

print(f"材料里的旧值：run={old_run} calls={old_calls} seconds={old_seconds} build={old_build}")

RULES: list[tuple[str, str]] = []
if old_run and old_run != run_id:
    RULES.append((old_run, run_id))
if old_calls and old_calls != calls:
    RULES += [
        (f"模型参与 {old_calls} 次", f"模型参与 {calls} 次"),
        (f"{old_calls} 次调用", f"{calls} 次调用"),
    ]
if old_seconds and old_seconds != seconds:
    RULES += [
        (f"全程 {old_seconds} 秒", f"全程 {seconds} 秒"),
        (f"全程约 {round(float(old_seconds)):d} 秒", f"全程约 {round(float(seconds)):d} 秒"),
        (f"{old_seconds} 秒、{old_calls or calls} 次调用", f"{seconds} 秒、{calls} 次调用"),
    ]
if old_build and old_build != build_time:
    RULES.append((f"2026-09-18 {old_build}", f"2026-09-18 {build_time}"))

# tokens 旧值（数据驱动：材料里出现的非当前口径的 "A + B tokens" 组合一律替换为明细口径；
# 比较时忽略千分位——明细源写 5808，材料惯写 5,808，属同一口径不算旧值）
if in_tok and out_tok:
    def _num(s: str) -> str:
        return s.replace(",", "")

    true_pair = (_num(in_tok), _num(out_tok))
    stale_tokens: set[str] = set()
    for f in MATERIALS:
        if not f.exists():
            continue
        for m in re.finditer(r"([\d,]+) ?\+ ?([\d,]+) tokens", f.read_text(encoding="utf-8")):
            if (_num(m.group(1)), _num(m.group(2))) != true_pair:
                stale_tokens.add(m.group(0))
    RULES += [(cand, f"{in_tok} + {out_tok}") for cand in sorted(stale_tokens)]

# 「X 秒 / Y 次」合并写法（如转储遗留事项里的冻结行；「Y 次 / X 秒」的截图波动口径不受影响）
for f in MATERIALS:
    if not f.exists():
        continue
    for m in re.finditer(r"([\d.]+) 秒 / (\d+) 次", f.read_text(encoding="utf-8")):
        if (m.group(1), m.group(2)) != (seconds, calls):
            RULES.append((m.group(0), f"{seconds} 秒 / {calls} 次"))

changed = 0
for target in MATERIALS:
    if not target.exists():
        continue
    t = target.read_bytes().decode("utf-8")
    before = t
    hits = []
    for old, new in RULES:
        if old in t:
            hits.append(old)
            t = t.replace(old, new)
    if t != before:
        target.write_bytes(t.encode("utf-8"))
        changed += 1
        print(f"已更新 {target.name}：{len(hits)} 处")
print("完成，更新文件数：", changed)
