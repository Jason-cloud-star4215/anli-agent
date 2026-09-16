"""工具层：注册、白名单、参数校验、权限控制与执行。

对应 BACKEND_STRUCTURE.md 第 2 节与第 5.2 节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .models import LegalProvision, Role


class ToolError(RuntimeError):
    pass


class UnknownTool(ToolError):
    pass


class ToolNotAllowed(ToolError):
    pass


class InvalidArguments(ToolError):
    pass


class ApprovalRequired(ToolError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, type]
    handler: Callable[..., Any]
    risk: str = "low"  # low | high


@dataclass
class ToolCall:
    tool: str
    arguments: dict[str, Any]


@dataclass
class TraceEntry:
    agent: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    detail: str = ""


class ToolRegistry:
    """工具注册表，同时充当 Tool Allowlist。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"工具重复注册：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise UnknownTool(f"工具不存在：{name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def validate(self, call: ToolCall) -> Tool:
        tool = self.get(call.tool)
        for key, expected_type in tool.schema.items():
            if key not in call.arguments:
                raise InvalidArguments(f"缺少参数：{key}")
            value = call.arguments[key]
            if not isinstance(value, expected_type):
                raise InvalidArguments(
                    f"参数类型错误：{key} 期望 {expected_type.__name__}，实际 {type(value).__name__}"
                )
        return tool


ROLE_ALLOWLIST: dict[Role, set[str]] = {
    Role.RESEARCHER: {"law_lookup", "case_search", "law_source_check", "official_case_search"},
    Role.CLAIMANT: {"law_lookup", "case_search", "calc_limitation", "calc_damages"},
    Role.DEFENDANT: {"law_lookup", "case_search", "calc_limitation", "calc_damages"},
    Role.JUDGE: {"law_lookup", "case_search", "calc_damages"},
    Role.REDTEAM: {"law_lookup", "case_search", "calc_limitation", "law_source_check"},
}


class ToolRouter:
    """执行工具并记录 Trace，同时执行白名单与权限校验。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.trace: list[TraceEntry] = []

    def call(
        self,
        role: Role,
        call: ToolCall,
        approved: bool = False,
    ) -> Any:
        tool = self.registry.validate(call)
        allowlist = ROLE_ALLOWLIST.get(role, set())
        if call.tool not in allowlist:
            self.trace.append(
                TraceEntry(role.value, call.tool, call.arguments, False, "不在白名单内")
            )
            raise ToolNotAllowed(f"角色 {role.value} 无权调用工具 {call.tool}")
        if tool.risk == "high" and not approved:
            self.trace.append(
                TraceEntry(role.value, call.tool, call.arguments, False, "需要人工确认")
            )
            raise ApprovalRequired(f"高风险工具需要人工确认：{call.tool}")
        try:
            result = tool.handler(**call.arguments)
        except Exception as exc:  # noqa: BLE001 - 统一转换为工具错误
            self.trace.append(
                TraceEntry(role.value, call.tool, call.arguments, False, str(exc))
            )
            raise ToolError(f"工具执行失败：{call.tool} :: {exc}") from exc
        self.trace.append(TraceEntry(role.value, call.tool, call.arguments, True))
        return result
