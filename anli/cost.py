"""单任务成本估算。

一条纪律：**没有单价就不猜价格**。

配置了单价（`pricing.json` 或环境变量）才给金额；没配置就只报调用次数、失败次数与耗时，
宁可少说也不编一个看起来专业的数字。接口没返回 token 用量时同理，如实写「未提供」。

单价查找顺序：

1. 环境变量 `ANLI_PRICE_INPUT_PER_MILLION` / `ANLI_PRICE_OUTPUT_PER_MILLION` / `ANLI_PRICE_CURRENCY`
2. 可写目录（exe 同级 / 运行目录）的 `pricing.json`
3. 包内 `data/pricing.json`（默认不带，留给用户自己填）
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import resource_path, user_root

ENV_INPUT = "ANLI_PRICE_INPUT_PER_MILLION"
ENV_OUTPUT = "ANLI_PRICE_OUTPUT_PER_MILLION"
ENV_CURRENCY = "ANLI_PRICE_CURRENCY"
DEFAULT_CURRENCY = "CNY"


@dataclass
class CostSummary:
    """一次任务的模型用量与成本。跨模块契约，字段稳定。"""

    calls: int = 0                # 成功的调用次数
    attempts: int = 0             # 实际发出的请求次数（含失败）
    failures: int = 0
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None     # 未配置单价时为 None，而不是 0
    currency: str = DEFAULT_CURRENCY
    pricing_source: str = "未配置"
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def candidate_pricing_files() -> list[Path]:
    """单价文件的查找位置，前者优先。"""

    paths = [user_root() / "pricing.json", Path.cwd() / "pricing.json", resource_path("data") / "pricing.json"]
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def load_pricing() -> tuple[dict | None, str]:
    """返回 (单价配置, 来源说明)。找不到返回 (None, "未配置")。"""

    env_input = os.environ.get(ENV_INPUT)
    env_output = os.environ.get(ENV_OUTPUT)
    if env_input or env_output:
        return (
            {
                "input_per_million": float(env_input or 0),
                "output_per_million": float(env_output or 0),
                "currency": os.environ.get(ENV_CURRENCY, DEFAULT_CURRENCY),
            },
            "环境变量",
        )

    for path in candidate_pricing_files():
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if "input_per_million" not in raw and "output_per_million" not in raw:
            continue
        return raw, path.name
    return None, "未配置"


def _cost_from(pricing: dict, prompt_tokens: int, completion_tokens: int) -> float:
    price_in = float(pricing.get("input_per_million") or 0)
    price_out = float(pricing.get("output_per_million") or 0)
    return round(
        prompt_tokens / 1_000_000 * price_in + completion_tokens / 1_000_000 * price_out, 4
    )


def summarize(llm=None, pricing: dict | None = None, pricing_source: str = "") -> CostSummary:
    """把模型客户端上的用量整理成一份汇总，并生成给用户看的一句话。"""

    if pricing is None and not pricing_source:
        pricing, pricing_source = load_pricing()

    calls = int(getattr(llm, "calls", 0) or 0)
    attempts = int(getattr(llm, "attempts", 0) or 0) or calls
    failures = int(getattr(llm, "failures", 0) or 0)
    seconds = round(float(getattr(llm, "seconds", 0.0) or 0.0), 1)
    usage = getattr(llm, "usage", None) or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)

    currency = str((pricing or {}).get("currency") or DEFAULT_CURRENCY)
    cost: float | None = None
    if calls == 0:
        cost = 0.0
    elif pricing is not None and (prompt_tokens or completion_tokens):
        cost = _cost_from(pricing, prompt_tokens, completion_tokens)

    summary = CostSummary(
        calls=calls,
        attempts=attempts,
        failures=failures,
        seconds=seconds,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
        currency=currency,
        pricing_source=pricing_source or ("未配置" if pricing is None else "已配置"),
    )
    summary.text = render_line(summary)
    return summary


def render_line(summary: CostSummary) -> str:
    """报告与页面展示用的一句话。不含推测成分。"""

    if summary.calls == 0 and summary.attempts == 0:
        return "本次分析：未调用大模型（规则版），模型成本 0 元。"

    parts = [f"模型调用 {summary.calls} 次"]
    if summary.failures:
        parts.append(f"其中失败 {summary.failures} 次")
    parts.append(f"耗时 {summary.seconds:.1f} 秒")

    if summary.cost is None:
        if summary.pricing_source == "未配置":
            detail = "未配置单价，故不估算金额（要估算请在 pricing.json 或环境变量里填单价）"
        else:
            detail = "接口未返回 token 用量，无法估算金额"
    else:
        tokens = summary.prompt_tokens + summary.completion_tokens
        detail = f"约 {tokens:,} tokens，估算成本 {summary.cost:.4f} {summary.currency}"

    return "本次分析：" + "、".join(parts) + f"；{detail}。"


def render_note(summary: CostSummary) -> str:
    """成本口径说明，跟着成本行一起展示，避免被当成账单。"""

    return "成本说明：按当前单价估算的参考值，实际以服务商账单为准。"
