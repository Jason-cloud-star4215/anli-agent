"""国家法律法规数据库（flk.npc.gov.cn）运行时适配器。

用途：把「这部法到底有没有、什么时候公布和施行、官方页面在哪」做成一次真实的
外部工具调用，而不是靠模型记忆或本地库自说自话。

能力边界（务必诚实）：
- 官方接口能拿到：法律标题、公布日期、施行日期、制定机关、条目编号（bbbs）、官方详情页；
- 官方接口拿不到：条文正文（正文在 oss 文件里，且外网不可达）。
  因此本模块的结果**不能**让一条引用从不核验变成可引用，只能作为「官方库收录情况」的佐证。

可用性设计：
- 原生实现（只用标准库），任何异常都转成 `LawSourceCheck(error=...)`，绝不让主流程中断；
- 结果按 TTL 缓存到本地，断网时继续用缓存，并在结果里标注是否是缓存与抓取时间。
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import LawSourceCheck

FLK_BASE = "https://flk.npc.gov.cn"
SEARCH_LIST = f"{FLK_BASE}/law-search/search/list"
LAW_DETAIL = f"{FLK_BASE}/law-search/search/flfgDetails"
DEFAULT_TTL_DAYS = 7
DEFAULT_TIMEOUT = 6

_TAG = re.compile(r"<[^>]+>")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_title(raw: str) -> str:
    """去掉接口返回里的高亮标签。"""

    return _TAG.sub("", raw or "").strip()


def normalize_title(title: str) -> str:
    """用于比对的归一化标题：去掉书名号、空白与「中华人民共和国」前缀。"""

    text = clean_title(title).replace("《", "").replace("》", "")
    text = re.sub(r"\s+", "", text)
    for prefix in ("中华人民共和国", "中国"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
    return text


def _post_json(url: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def search_law(title: str, page_size: int = 5, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """按标题检索官方库，返回条目摘要。"""

    payload = {
        "searchContent": clean_title(title),
        "pageNum": 1,
        "pageSize": page_size,
        "searchType": 1,
        "searchRange": 1,
    }
    result = _post_json(SEARCH_LIST, payload, timeout=timeout)
    rows = result.get("rows") or []
    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "bbbs": row.get("bbbs") or "",
                "title": clean_title(row.get("title") or ""),
                "publish_date": row.get("gbrq") or "",
                "effective_date": row.get("sxrq") or "",
                "organ": row.get("zdjgName") or "",
                "law_type": row.get("flxz") or "",
                "detail_url": law_detail_url(row.get("bbbs") or ""),
            }
        )
    return cleaned


def pick_best_match(query: str, rows: list[dict]) -> dict | None:
    """从检索结果里挑出与查询最匹配的一条，挑不准就返回 None。"""

    target = normalize_title(query)
    if not target or not rows:
        return None
    for row in rows:
        if normalize_title(row.get("title") or "") == target:
            return row
    for row in rows:
        candidate = normalize_title(row.get("title") or "")
        if candidate and (target in candidate or candidate in target):
            return row
    return None


def law_detail(bbbs: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """取回某部法律的官方条目详情（含章节与条号目录树，不含条文正文）。"""

    request = urllib.request.Request(f"{LAW_DETAIL}?bbbs={urllib.parse.quote(bbbs)}")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def law_detail_url(bbbs: str) -> str:
    """生成可点击的官方详情页链接。"""

    if not bbbs:
        return FLK_BASE
    encoded = urllib.parse.quote(_b64(bbbs))
    return f"{FLK_BASE}/detail2.html?{encoded}"


def _b64(value: str) -> str:
    import base64

    return base64.b64encode(value.encode("utf-8")).decode("ascii")


class LawSourceChecker:
    """带缓存与降级的官方库核对器。线程安全，可被多个任务共用。"""

    def __init__(
        self,
        cache_path: str | Path | None = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
        timeout: int = DEFAULT_TIMEOUT,
        enabled: bool = True,
        fetch=None,
    ) -> None:
        self.cache_path = Path(cache_path) if cache_path else None
        self.ttl = timedelta(days=ttl_days)
        self.timeout = timeout
        self.enabled = enabled
        self._fetch = fetch or search_law
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._loaded = False
        self.calls = 0

    # ------------------------------------------------------------------ 缓存
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.cache_path or not self.cache_path.exists():
            return
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            entries = raw.get("entries") or {}
            if isinstance(entries, dict):
                self._cache = entries
        except (OSError, ValueError):
            self._cache = {}

    def _save(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "entries": self._cache}
            self.cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _fresh(self, entry: dict) -> bool:
        stamp = entry.get("fetched_at") or ""
        try:
            fetched = datetime.fromisoformat(stamp)
        except ValueError:
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched < self.ttl

    # ------------------------------------------------------------------ 核对
    def check(self, law_name: str) -> LawSourceCheck:
        """核对一部法的官方收录情况；任何失败都返回带 error 的结果。"""

        name = clean_title(law_name)
        if not name:
            return LawSourceCheck(law_name=law_name, error="未提供法律名称")
        if not self.enabled:
            return LawSourceCheck(law_name=name, error="未启用官方库核对")

        key = normalize_title(name)
        with self._lock:
            self._load()
            entry = self._cache.get(key)
            if entry and self._fresh(entry):
                return self._from_entry(name, entry, from_cache=True)

        try:
            self.calls += 1
            rows = self._fetch(name, page_size=5, timeout=self.timeout)
        except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
            detail = str(getattr(exc, "reason", exc)) or exc.__class__.__name__
            if entry:
                # 断网时退回缓存，但明确标注这是缓存
                return self._from_entry(name, entry, from_cache=True, error=f"实时核对失败：{detail}")
            return LawSourceCheck(law_name=name, error=f"官方库不可用：{detail}"[:80])

        best = pick_best_match(name, rows)
        if not best:
            result = LawSourceCheck(law_name=name, fetched_at=_now())
        else:
            result = LawSourceCheck(
                law_name=name,
                found=True,
                official_title=best.get("title") or "",
                publish_date=best.get("publish_date") or "",
                effective_date=best.get("effective_date") or "",
                organ=best.get("organ") or "",
                detail_url=best.get("detail_url") or "",
                fetched_at=_now(),
            )
        with self._lock:
            self._cache[key] = asdict(result)
            self._save()
        return result

    @staticmethod
    def _from_entry(
        law_name: str, entry: dict, from_cache: bool, error: str = ""
    ) -> LawSourceCheck:
        fields = {
            key: entry.get(key)
            for key in (
                "found",
                "official_title",
                "publish_date",
                "effective_date",
                "organ",
                "detail_url",
                "fetched_at",
            )
        }
        fields["found"] = bool(fields.get("found"))
        return LawSourceCheck(
            law_name=law_name,
            from_cache=from_cache,
            error=error,
            **fields,
        )
