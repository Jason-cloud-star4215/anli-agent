"""大模型客户端：OpenAI 兼容的 chat/completions 接口。

密钥来源优先级：环境变量 > 项目根目录 .env 文件。
只用标准库实现，避免引入额外依赖。
"""

from __future__ import annotations

import json
import os
import re
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
        # 成本与耗时统计：calls 只数成功，attempts 数实际发出的请求，
        # seconds 与 usage 用于报告末尾的成本行（见 anli/cost.py）
        self.attempts = 0
        self.seconds = 0.0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @property
    def available(self) -> bool:
        return bool(self.api_key)

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
        self.attempts += 1
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            raise LLMError(f"模型接口返回 {exc.code}：{detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"模型接口调用失败：{exc}") from exc
        finally:
            # 失败的调用同样耗时、同样计费风险，必须计入
            self.seconds += time.monotonic() - started

        self.calls += 1
        self._merge_usage(body.get("usage"))
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"模型返回结构异常：{str(body)[:200]}") from exc

    def _merge_usage(self, usage: object) -> None:
        """累加接口返回的 token 用量。服务端不给 usage 时保持为 0，报告里如实写「未提供」。"""

        if not isinstance(usage, dict):
            return
        for key in self.usage:
            value = usage.get(key)
            if isinstance(value, (int, float)):
                self.usage[key] += int(value)

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
