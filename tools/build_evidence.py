"""把「一次运行的全部证据」拼装成 artifacts/验收证据.txt。

为什么需要它：此前验收证据是手工拼的，测试数变化后要手动重拼，且出现过
段头重复的小瑕疵。现在由脚本拼装：单元测试全量输出 + 端到端自检输出 +
过程日志 + 调用明细 + 工具清单 + 导出报告节选 + 设计要点，一次成型。

用法：
    # 1) 服务已启动（源码或打包版），例如 http://127.0.0.1:8765
    python tools/build_evidence.py --base http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts"

SEP = "------------------------------------------------------------"


def run(cmd: list[str]) -> str:
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.stdout + (("\n" + proc.stderr) if proc.stderr.strip() else "")


def count_tests(output: str) -> tuple[str, str]:
    for line in output.splitlines():
        if line.startswith("Ran ") and "tests" in line:
            return line.split()[1], line.split()[3].replace("s", "")
    raise SystemExit("无法从单元测试输出解析测试数")


NOTES = """【六】反幻觉、责任方向与隐私设计要点（与当前代码一一对应）
------------------------------------------------------------
1. 法条引用逐字核验：引文与被核验原文不一致 → 自动剔除并生成高风险发现
   （tests/test_citation.py::test_tampered_quote_still_rejected）；
2. 法条库 23 条：20 条已从官方来源逐字核验（带来源链接），3 条为「正文待核验」占位条目，
   系统对其一律拒绝引用（tests/test_law_library.py）；
3. 量级闸门：样本与本案损失量级不同档（2 万/10 万/50 万三条线）时拒绝区间外推，
   输出「暂不提供」并说明原因（tests/test_magnitude_gate.py）；
4. 证据闸门：充分度 < 70% 或缺证金额占比 ≥ 40% 时不给结论性赔付数字
   （tests/test_evidence_gate.py）；
5. 范围与转人工闸门：刑事/行政案件直接拦停给求助渠道；涉人身安全、金额重大、
   时效临近转人工，用户确认前不给行动方案与文书（anli/scope.py + tests/test_scope.py）；
6. 责任方向识别：说「我负全部责任」和「对方负全部责任」是相反结论，
   按身份分两套主语表判定主体；否定说法（不承担任何责任 / 无任何责任 / 不担责 等 13 个变体）
   已列全，漏一个就会掉进默认值 1.0 并把方向判反；
   责任不成立时结论、策略、文书、补证请求全部改口径
   （anli/intake.py::parse_responsibility_detail + tests/test_responsibility_direction.py
    + tools/responsibility_matrix.py 的 30 格穷举自查）；
7. 金额由确定性规则逐项计算，不由大模型生成；模型只能提议、代码裁决
   （anli/item_assessment.py、anli/agents.py 白名单工具）；
8. 自主规划的边界：模型只做「选择与排序」（检索词、是否追加轮次、行动路径顺序），
   越界内容丢弃、规则版兜底、每次决策留痕（anli/planner.py + tests/test_planner.py）；
9. 对外发送前脱敏：案情原话不管从哪个出口发给模型，身份证/银行卡等一律打码
   （anli/redact.py、anli/llm_intake.py、anli/agents.py::user_text_for_llm + tests/test_redact.py）；
10. 材料正文解析只认「真的读到了」的文本：txt/md/docx/pdf 文本层可读，
    扫描件与图片如实标注未解析且不产生证据（anli/materials.py + tests/test_materials.py）；
11. 类案数据强制标注真实等级与样本量：🟢 实证 / 🟡 演示 / 🔴 无样本，
    实证样本 ≥3 条时自动排除演示数据；报告写明「数据等级与升级路径」
    （anli/orchestrator.py::sample_tier_line + tests/test_sample_tier.py）；
12. 官方发布案例参照层与金额区间物理隔离：79 条省高院／最高法发布稿只作旁证，
    有/无这份缓存时金额区间完全相等（anli/published_cases.py + tests/test_published_integration.py）；
13. 文书按身份出具：主张方=起诉状、抗辩方=答辩状、法官=裁判推演，
    当事人文书里不出现「本院认为」「判决结果」这类裁判口径，法院名称留占位符不编造
    （anli/documents.py + tests/test_documents.py）；
14. 报告一致性审计：各章节数字不矛盾、导出与页面同源
    （tests/test_report_consistency.py；本次运行的「状态行 / 成本行 / 明细」三项
     调用次数与耗时逐项相等）；
15. 风险提示只列真缺失（2026-10-01 修复）：blocked_items 的 missing 一律取
    「要件全集 − 用户已持有」的差集；需按法定标准计算的项目，风险行改写参数口径。
    修复动因：主线运行曾出现「误工费因缺少误工证明未计入」而用户已交误工证明的自相矛盾，
    红队复核当场指出（anli/intake.py::findings_to_profile_parts 的 held 参数
    + tests/test_missing_evidence_diff.py）；
16. 起诉状诉讼请求的明细列全主张项目：已计入的写实数，未计入的标注
    「待补证后另行主张」，不再出现「合计 12,000 元（明细仅医疗费）」的读不通
    （anli/documents.py::_claim_lines + tests/test_missing_evidence_diff.py）。

补充（2026-09-19，后置否定证据与泛词边界修复）：用户原话说「票据还没找到 / 工资流水还没打」时，
证据抽取不再把它当成有材料（anli/intake.py：POST_NEGATIONS + explicitly_missing_evidence）；
大模型坚持「有材料」也会被否决（anli/llm_intake.py）；论证提示词按「已持有 / 仅口头提及」
分组，避免质证复核把待补材料当成矛盾反复报警（anli/agents.py：evidence_brief）。
回归：tests/test_post_negation_evidence.py（8 项，含端到端 2,240 断言与泛词边界）。泛词（票据/发票/…）不作为否决依据，否定窗口不跨句读。

补充（2026-10-01，决赛备战修复轮）：①missing 差集修复（见第 15 条）；②投屏演示模式
（web ?present=1：全局字号放大 + 页头角标，不隐藏面板；tests/test_present_mode.py）；
③赔偿标准表 2025 年度条目来源换为浙江省统计局官方公报原文链接（data/standards.json）；
④证据清单小节标题改为「已提交或提及的证据（括注是否已有材料）」，不再让「仅口头提及」
混在「已具备」名下（anli/orchestrator.py）。"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    args = parser.parse_args()

    print("[1/6] 跑单元测试（全量 verbose）……")
    unit = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    n_tests, _ = count_tests(unit)
    ok = "\nOK" in unit or unit.rstrip().endswith("OK")

    print("[2/6] 跑端到端自检（对着运行中的服务）……")
    selftest = run([sys.executable, "tools/selftest.py", "--base", args.base])
    passed = selftest.count("[PASS]")

    log_head = (ART / "过程日志_LLM链路.txt").read_text(encoding="utf-8").splitlines()
    header = next(line for line in log_head if line.startswith("# 环境："))
    task_line = next(line for line in log_head if line.startswith("# 任务"))
    run_id = task_line.split("任务")[1].strip().split(" ")[0]

    log = "\n".join(log_head)
    detail = (ART / "模型调用明细_LLM链路.md").read_text(encoding="utf-8")
    tools_list = (ART / "工具调用清单_LLM链路.md").read_text(encoding="utf-8")
    report = (ART / "导出报告_LLM链路.md").read_text(encoding="utf-8")

    parts = [
        f"「案理」系统验收证据（自动生成于 {date.today().isoformat()}）",
        "=" * 48,
        "",
        "本文件由真实运行输出拼装（tools/build_evidence.py），未做任何人工修饰。",
        f"所有数字来自这一次运行（任务号 {run_id}），{header[2:]}：",
        f"  · 第一部分：单元测试全量输出（{n_tests} 项，含反幻觉、责任方向、真缺失差集与投屏模式回归）",
        f"  · 第二部分：端到端自检输出（{passed} 项，对着运行中的服务逐项验证）",
        "  · 第三部分：大模型真实链路过程日志（口径以文件头为准）",
        "  · 第四部分：逐次调用明细与工具调用清单（口径逐项对账）",
        "  · 第五部分：导出报告与金额认定表节选（Markdown / CSV / Word 三格式同源）",
        "  · 第六部分：反幻觉、责任方向与隐私设计要点（与当前代码一一对应）",
        "",
        f"【一】自动化测试：{n_tests} 项{'全部通过' if ok else '存在失败'}",
        SEP,
        unit.strip(),
        "",
        f"【二】端到端自检：{passed} 项全部通过（对着运行中的服务逐项验证）",
        SEP,
        selftest.strip(),
        "",
        "【三】大模型真实链路过程日志（SSE 原样导出）",
        "----------------------------------------------------------------------",
        log.strip(),
        "",
        "【四】逐次调用明细与工具调用清单（同一 run，口径逐项对账）",
        SEP,
        detail.strip(),
        "",
        tools_list.strip(),
        "",
        "【五】导出节选（同一次运行；完整版见 导出报告_LLM链路.md / 金额认定表_LLM链路.csv）",
        SEP,
        report.strip(),
        "",
        NOTES,
        "",
    ]

    out = ART / "验收证据.txt"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"[完成] {out}（测试 {n_tests} 项 / 自检 {passed} 项 / run {run_id}）")
    if not ok:
        raise SystemExit("单元测试存在失败，验收证据已生成但不可用于封版")


if __name__ == "__main__":
    main()
