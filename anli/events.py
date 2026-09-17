"""结构化事件流。

对应 BACKEND_STRUCTURE.md 第 4 节：只发送结构化事件，不发送裸字符串。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


EVENT_TYPES = (
    "task_created",
    "stage_changed",
    "retrieval_started",
    "retrieval_query",
    "retrieval_hit",
    "retrieval_finished",
    "citation_verified",
    "agent_started",
    "agent_delta",
    "agent_message",
    "llm_state",
    "plan_decided",
    "round_started",
    "critic_finding",
    "evidence_requested",
    "approval_required",
    "artifact_ready",
    "task_completed",
    "task_failed",
)


@dataclass
class Event:
    type: str
    task_id: str
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if self.type not in EVENT_TYPES:
            raise ValueError(f"未定义的事件类型：{self.type}")


Handler = Callable[[Event], None]


class EventBus:
    """极简事件总线，前端可通过 WebSocket 或 SSE 订阅同一批事件。"""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._handlers: list[Handler] = []
        self.history: list[Event] = []

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def emit(self, event_type: str, task_id: str, payload: dict[str, Any] | None = None) -> Event:
        event = Event(
            type=event_type,
            task_id=task_id,
            sequence=next(self._counter),
            payload=payload or {},
        )
        self.history.append(event)
        for handler in self._handlers:
            handler(event)
        return event
