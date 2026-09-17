"""大模型客户端：OpenAI 兼容的 chat/completions 接口。

密钥来源优先级：环境变量 > 项目根目录 .env 文件。
只用标准库实现，避免引入额外依赖。
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

from .paths import user_root

ENV_FILE = user_root() / ".env"


def candidate_env_files() -> list[Path]:
    """按优先级列出 .env 的查找位置。

    打包成 exe 后工作目录可能是任意位置，因此依次查：
    exe 同级目录 → 当前工作目录 → 源码目录。
    """

    paths = [ENV_FILE, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class LLMError(RuntimeError):
    """大模型调用失败。"""


def load_env_file(path: Path | None = None) -> dict[str, str]:
    candidates = [path] if path is not None else candidate_env_files()
    values: dict[str, str] = {}
    for candidate in candidates:
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip())
    return values


def get_config() -> dict[str, str]:
    file_values = load_env_file()
    return {
        "base_url": os.environ.get("ANLI_LLM_BASE_URL")
        or file_values.get("ANLI_LLM_BASE_URL", DEFAULT_BASE_URL),
        "model": os.environ.get("ANLI_LLM_MODEL")
        or file_values.get("ANLI_LLM_MODEL", DEFAULT_MODEL),
        "api_key": os.environ.get("ANLI_LLM_API_KEY")
        or file_values.get("ANLI_LLM_API_KEY", ""),
    }


class LLMClient:
    """最小可用的大模型客户端。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 90,
    ) -> None:
        config = get_config()
        self.base_url = (base_url or config["base_url"]).rstrip("/")
        # 显式传空串表示「就是没有密钥」，只有传 None 才回落到配置文件
        self.api_key = config["api_key"] if api_key is None else api_key
        self.model = model or config["model"]
        self.timeout = timeout
        self.calls = 0
        self.failures = 0
        # 带刹车重试 + 熔断：
        # - 429/5xx/连接类失败自动重试一次（超时类用缩水超时重试）；
        # - 同一任务内连续 2 次最终失败即熔断，后续调用秒退规则版，不再逐个等超时；
        # - last_failure 留痕失败原因，供诊断与横幅展示。
        self.retries = 0
        self.consecutive_failures = 0
        self.last_failure = ""
        self.retry_backoff = 1.5
        self.breaker_threshold = 2

    @property
    def circuit_open(self) -> bool:
        return self.consecutive_failures >= self.breaker_threshold

    @property
    def available(self) -> bool:
        return bool(self.api_key) and not self.circuit_open

    # ------------------------------------------------------------------ 基础调用
    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 1500,
    ) -> str:
        if not self.available:
            raise LLMError("未配置 API Key，无法调用大模型")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json;charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        timeout = self.timeout
        last_reason = ""
        for attempt in (0, 1):  # 首次 + 最多一次自动重试
            response_body = None
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                    response_body = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")[:300]
                last_reason = f"HTTP {exc.code}：{detail[:120]}"
                # 429 限流与 5xx 服务端错误可重试；401/402/400 这类配置性问题不重试
                if (exc.code == 429 or exc.code >= 500) and attempt == 0:
                    self._before_retry(self._retry_after(exc))
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or (
                    "timed out" in str(exc).lower()
                )
                last_reason = "请求超时" if timed_out else f"连接失败：{exc}"
                if attempt == 0:
                    self._before_retry(self.retry_backoff)
                    timeout = min(self.timeout, 30)  # 缩水超时重试，压住最坏等待
                    continue
                break
            else:
                self.calls += 1
                self.consecutive_failures = 0
                try:
                    return response_body["choices"][0]["message"]["content"]
                except (KeyError, IndexError) as exc:
                    self.consecutive_failures += 1
                    self.last_failure = "模型返回结构异常"
                    raise LLMError(f"模型返回结构异常：{str(response_body)[:200]}") from exc

        self.consecutive_failures += 1
        self.last_failure = last_reason
        raise LLMError(f"模型接口调用失败：{last_reason}")

    def _before_retry(self, wait: float) -> None:
        self.retries += 1
        time.sleep(max(0.0, wait))

    def _retry_after(self, exc: urllib.error.HTTPError) -> float:
        """尊重服务端 Retry-After（上限 10 秒），没有则用默认退避。"""

        try:
            value = exc.headers.get("Retry-After") if exc.headers else None
            if value:
                return min(float(value), 10.0)
        except (TypeError, ValueError):
            pass
        return self.retry_backoff

    # ------------------------------------------------------------------ JSON 调用
    def chat_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_tokens: int = 1500,
        retries: int = 2,
    ) -> dict:
        """要求模型只输出 JSON，并解析成字典。解析失败会重试一次。"""

        last_error: Exception | None = None
        for _ in range(retries + 1):
            try:
                text = self.chat(
                    system, user, temperature=temperature, max_tokens=max_tokens
                )
                return extract_json(text)
            except (LLMError, ValueError) as exc:
                # JSON 被截断或格式不对时也要重试：
                # 只捕获 LLMError 会让「最后一个 } 被截掉」直接掉进规则版兜底
                last_error = exc
        self.failures += 1
        self.last_failure = f"JSON 解析失败：{last_error}"
        raise LLMError(f"无法解析模型返回的 JSON：{last_error}")


def extract_json(text: str) -> dict:
    """从模型输出里抠出第一个 JSON 对象。"""

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"模型没有返回 JSON：{text[:200]}")
    return json.loads(cleaned[start : end + 1])
