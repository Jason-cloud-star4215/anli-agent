"""上传材料的正文解析。

补齐的是 PRD 里一直没兑现的那条承诺：材料上传后**只用了文件名**参与证据识别，
正文从未被打开。这里把正文读出来，抽出金额、日期、案号、法院与证据线索。

只用标准库，因此能力边界必须说清楚（`MaterialFacts.reason` 就是给用户看的）：

| 格式 | 能否解析 | 说明 |
| --- | --- | --- |
| txt / md / csv / json / log | ✅ | 直接读文本，自动尝试 UTF-8 与 GBK |
| docx | ✅ | 读 `word/document.xml` 去标签 |
| pdf | ⚠️ 有限 | 只能抽文本层；扫描件、以及使用自定义 CID 编码的 PDF 抽不出来 |
| 图片 / 扫描件 | ❌ | 无 OCR 能力，明确标注未解析 |

**解析不到就标注未解析，绝不假装读到了内容。**
原文本不落进检查点与视图：只保留脱敏后的片段，避免案情原文随任务落盘。
"""

from __future__ import annotations

import json
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .material_text import (
    extract_case_id,
    extract_court,
    extract_dates,
    extract_evidence_tags,
    find_amount_candidates,
)
from .redact import redact

# 单个文件解析上限：大文件没必要全读，读前 200KB 足够覆盖要点
DEFAULT_LIMIT_BYTES = 200_000
# 摘录长度：够用户确认「系统确实读到了」，又不至于把整份材料带进视图
EXCERPT_CHARS = 200
# PDF 文本层低于这个字数就认为没抽到东西。
# 定得不高：票据类 PDF 本来就短，而真正的扫描件根本解不出内容流（更早就被拦下）。
MIN_PDF_CHARS = 12

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log", ".text"}
DOCX_SUFFIX = ".docx"
PDF_SUFFIX = ".pdf"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".heic"}


@dataclass
class MaterialFacts:
    """一份材料的解析结果。跨模块契约，字段稳定。"""

    name: str                       # 原始文件名
    kind: str                       # txt / docx / pdf / 图片 / 其他
    parsed: bool                    # 是否真的读到了正文
    reason: str = ""                # parsed 为 False 时的原因，直接展示给用户
    amounts: list[float] = field(default_factory=list)      # 正文中的金额候选
    dates: list[str] = field(default_factory=list)
    case_no: str = ""
    court: str = ""
    evidence_tags: list[str] = field(default_factory=list)
    text_chars: int = 0             # 读到多少字（只记数量，不存原文）
    excerpt: str = ""               # 脱敏后的开头片段，供用户确认

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "parsed": self.parsed,
            "reason": self.reason,
            "amounts": self.amounts,
            "dates": self.dates,
            "case_no": self.case_no,
            "court": self.court,
            "evidence_tags": self.evidence_tags,
            "text_chars": self.text_chars,
            "excerpt": self.excerpt,
        }


# ---------------------------------------------------------------------- 读取实现
def _read_text_file(path: Path, limit_bytes: int) -> tuple[str, str]:
    """读文本文件，返回 (正文, 失败原因)。UTF-8 失败再试 GBK。"""

    try:
        raw = path.read_bytes()[:limit_bytes]
    except OSError as exc:
        return "", f"读取失败：{exc}"
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding), ""
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), ""


def _read_docx(path: Path, limit_bytes: int) -> tuple[str, str]:
    """读 docx 正文：打开 zip 包，取 word/document.xml 去标签。"""

    try:
        with zipfile.ZipFile(path) as package:
            if "word/document.xml" not in package.namelist():
                return "", "不是有效的 Word 文档（缺少 word/document.xml）"
            xml = package.read("word/document.xml")[:limit_bytes * 8].decode("utf-8", "ignore")
    except (zipfile.BadZipFile, OSError) as exc:
        return "", f"无法打开 Word 文档：{exc}"
    # 段落结束换成换行，其余标签去掉；实体还原
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    text = (
        xml.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
    return re.sub(r"\n{3,}", "\n\n", text), ""


_PDF_TOKEN = re.compile(rb"\((?:\\.|[^\\()])*\)")


def _pdf_content_streams(raw: bytes, limit_bytes: int) -> bytes:
    """把 PDF 里能解压的内容流拼起来（只看前若干字节，避免大文件拖慢任务）。"""

    pieces: list[bytes] = []
    total = 0
    for match in re.finditer(rb"stream\r?\n", raw):
        start = match.end()
        end = raw.find(b"endstream", start)
        if end < 0:
            continue
        chunk = raw[start:end].rstrip(b"\r\n")
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            # 不是 Flate 编码：可能是未压缩的内容流，原样使用（乱码会在后续步骤被识别出来）
            pass
        pieces.append(chunk)
        total += len(chunk)
        if total >= limit_bytes:
            break
    return b"\n".join(pieces)


def _decode_pdf_token(token: bytes) -> str:
    """把一对括号里的内容解成文本，处理 PDF 的转义规则。"""

    body = token[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index : index + 1]
        if char == b"\\" and index + 1 < len(body):
            nxt = body[index + 1 : index + 2]
            if nxt == b"n":
                out += b"\n"
                index += 2
                continue
            if nxt in (b"r", b"t", b"b", b"f"):
                index += 2
                continue
            if nxt.isdigit():
                octal = body[index + 1 : index + 4]
                if len(octal) == 3 and octal.isdigit():
                    out.append(int(octal, 8) & 0xFF)
                    index += 4
                    continue
            out += nxt
            index += 2
            continue
        out += char
        index += 1
    return out.decode("utf-8", "replace")


def _looks_unreliable(text: str) -> bool:
    """判断抽出来的文本是不是乱码。

    中文 PDF 常用自定义 CID 编码，解出来是一串高位字节，
    这种情况下必须如实说「没抽出来」，而不是把乱码当正文。

    判定只用两条：控制符/替换符占比过高，或几乎找不到字母数字与汉字。
    注意不能按「是否 ASCII」来判断——中文标点、全角符号都是合法正文。
    """

    if not text:
        return True
    bad = sum(
        1
        for char in text
        if char == "\ufffd" or (not char.isprintable() and not char.isspace())
    )
    if bad / len(text) > 0.3:
        return True
    meaningful = sum(
        1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )
    return meaningful / len(text) < 0.2


def _read_pdf(path: Path, limit_bytes: int) -> tuple[str, str]:
    """PDF 文本层抽取。抽不到就返回失败原因，不返回半截乱码。"""

    try:
        raw = path.read_bytes()[: limit_bytes * 10]
    except OSError as exc:
        return "", f"读取失败：{exc}"

    streams = _pdf_content_streams(raw, limit_bytes)
    if not streams:
        return "", "PDF 里没有可直接读取的文本层（多为扫描件或加密文档），需要图片识别能力，本版本不支持"

    text = "".join(_decode_pdf_token(token) for token in _PDF_TOKEN.findall(streams))
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if len(text) < MIN_PDF_CHARS:
        return "", "PDF 文本层内容过少，未能可靠提取（多为扫描件）"
    if _looks_unreliable(text):
        return "", "PDF 使用了自定义字体编码，正文无法可靠提取；请改传 txt 或 docx，或手工补充要点"
    return text, ""


# ---------------------------------------------------------------------- 解析入口
def _facts_from_text(name: str, kind: str, text: str) -> MaterialFacts:
    case_no, _ = extract_case_id(text)
    return MaterialFacts(
        name=name,
        kind=kind,
        parsed=True,
        amounts=find_amount_candidates(text, limit=8),
        dates=extract_dates(text),
        case_no=case_no,
        court=extract_court(text),
        evidence_tags=extract_evidence_tags(text),
        text_chars=len(text),
        excerpt=redact(text[:EXCERPT_CHARS].replace("\n", " ").strip()),
    )


def parse_material(
    path: Path, raw_name: str = "", limit_bytes: int = DEFAULT_LIMIT_BYTES
) -> MaterialFacts:
    """解析一份材料。任何异常都转成「未解析 + 原因」，不向上抛。"""

    name = raw_name or path.name
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        text, error = _read_text_file(path, limit_bytes)
        if error:
            return MaterialFacts(name=name, kind="文本", parsed=False, reason=error)
        if not text.strip():
            return MaterialFacts(name=name, kind="文本", parsed=False, reason="文件是空的")
        return _facts_from_text(name, "文本", text)

    if suffix == DOCX_SUFFIX:
        text, error = _read_docx(path, limit_bytes)
        if error:
            return MaterialFacts(name=name, kind="Word", parsed=False, reason=error)
        if not text.strip():
            return MaterialFacts(name=name, kind="Word", parsed=False, reason="文档里没有正文")
        return _facts_from_text(name, "Word", text)

    if suffix == PDF_SUFFIX:
        text, error = _read_pdf(path, limit_bytes)
        if error:
            return MaterialFacts(name=name, kind="PDF", parsed=False, reason=error)
        return _facts_from_text(name, "PDF", text)

    if suffix in IMAGE_SUFFIXES:
        return MaterialFacts(
            name=name,
            kind="图片",
            parsed=False,
            reason="图片需要 OCR 能力，本版本不支持；请把关键信息（金额、日期）写在案情描述里",
        )

    return MaterialFacts(
        name=name,
        kind="其他",
        parsed=False,
        reason=f"暂不支持的格式（{suffix or '无扩展名'}），仅按文件名参与证据识别",
    )


def parse_materials(
    items: list[tuple[Path, str]], limit_bytes: int = DEFAULT_LIMIT_BYTES
) -> list[MaterialFacts]:
    return [parse_material(path, name, limit_bytes) for path, name in items]


# ---------------------------------------------------------------------- 结果汇总
def summarize(facts: list[MaterialFacts]) -> dict:
    """给视图用的一句话汇总 + 结构化统计。"""

    if not facts:
        return {"total": 0, "parsed": 0, "unparsed": 0, "text": ""}
    parsed = [item for item in facts if item.parsed]
    unparsed = [item for item in facts if not item.parsed]
    parts = [f"共 {len(facts)} 份材料"]
    if parsed:
        parts.append(f"已解析 {len(parsed)} 份")
    if unparsed:
        parts.append(f"未解析 {len(unparsed)} 份（{'、'.join(item.kind for item in unparsed)}）")
    return {
        "total": len(facts),
        "parsed": len(parsed),
        "unparsed": len(unparsed),
        "text": "，".join(parts),
    }


def evidence_from_materials(facts: list[MaterialFacts]) -> set[str]:
    """材料正文里出现的证据线索（只有真的读到正文的才算）。"""

    tags: set[str] = set()
    for item in facts:
        if item.parsed:
            tags |= set(item.evidence_tags)
    return tags


def cross_check_amounts(facts: list[MaterialFacts], claimed: dict[str, float]) -> list[str]:
    """把材料里的金额与用户自述金额对一遍，只报「对不上」的事实。

    不做任何推断：材料里有、描述里没写 → 提示确认；
    描述里有、材料里没出现 → 也提示确认。两者都可能是正常情况，
    所以措辞是「请确认」而不是「有误」。
    """

    described = {
        round(float(value))
        for value in (claimed or {}).values()
        if isinstance(value, (int, float)) and value
    }
    material_amounts: set[int] = set()
    for item in facts:
        if item.parsed:
            material_amounts |= {round(value) for value in item.amounts}

    notes: list[str] = []
    only_material = sorted(material_amounts - described, reverse=True)[:3]
    only_described = sorted(described - material_amounts, reverse=True)[:3]
    if only_material:
        notes.append(
            "材料里出现、但你在案情描述里没有提到的金额："
            + "、".join(f"{value:,.0f} 元" for value in only_material)
            + "，请确认是否属于本案损失"
        )
    if only_described and material_amounts:
        notes.append(
            "你在案情描述里提到、但已解析的材料中没有对应金额："
            + "、".join(f"{value:,.0f} 元" for value in only_described)
            + "，请确认是否另有凭证"
        )
    return notes


def load_facts(payload: object) -> list[MaterialFacts]:
    """从检查点里恢复解析结果（补交轮次复用，不重复解析）。"""

    if not isinstance(payload, list):
        return []
    facts: list[MaterialFacts] = []
    for item in payload:
        if isinstance(item, dict) and item.get("name"):
            facts.append(
                MaterialFacts(
                    name=str(item.get("name", "")),
                    kind=str(item.get("kind", "")),
                    parsed=bool(item.get("parsed")),
                    reason=str(item.get("reason", "")),
                    amounts=[float(v) for v in item.get("amounts", []) if isinstance(v, (int, float))],
                    dates=[str(v) for v in item.get("dates", [])],
                    case_no=str(item.get("case_no", "")),
                    court=str(item.get("court", "")),
                    evidence_tags=[str(v) for v in item.get("evidence_tags", [])],
                    text_chars=int(item.get("text_chars", 0) or 0),
                    excerpt=str(item.get("excerpt", "")),
                )
            )
    return facts


def dump_facts(facts: list[MaterialFacts]) -> str:
    """JSON 化，便于写进检查点。"""

    return json.dumps([item.to_dict() for item in facts], ensure_ascii=False)
