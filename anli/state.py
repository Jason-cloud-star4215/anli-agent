"""任务状态机与检查点。

对应 PRD 第 7 节与 BACKEND_STRUCTURE.md 第 5.3 节「硬边界」。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import TaskState


MAIN_CHAIN: list[TaskState] = [
    TaskState.RECEIVED,
    TaskState.CLARIFYING,
    TaskState.CLASSIFIED,
    TaskState.RESEARCHING,
    TaskState.DEBATING,
    TaskState.CRITIQUING,
    TaskState.EVALUATING,
    TaskState.DELIVERED,
]

TERMINAL_STATES = {TaskState.DELIVERED, TaskState.FAILED, TaskState.CANCELLED}

BRANCH_STATES = {TaskState.NEEDS_INFO, TaskState.NEEDS_EVIDENCE, TaskState.NEEDS_HUMAN}


def _build_transitions() -> dict[TaskState, set[TaskState]]:
    allowed: dict[TaskState, set[TaskState]] = {state: set() for state in TaskState}
    for index, state in enumerate(MAIN_CHAIN[:-1]):
        allowed[state].add(MAIN_CHAIN[index + 1])
    # 分支状态可从任意进行中的阶段进入，也可回到原阶段
    for state in MAIN_CHAIN:
        allowed[state] |= BRANCH_STATES
        allowed[state] |= {TaskState.FAILED, TaskState.CANCELLED}
    for branch in BRANCH_STATES:
        allowed[branch] |= set(MAIN_CHAIN) | {TaskState.FAILED, TaskState.CANCELLED}
    return allowed


ALLOWED_TRANSITIONS = _build_transitions()


class IllegalTransition(RuntimeError):
    """非法的状态转换。"""


class BudgetExceeded(RuntimeError):
    """超出轮次或预算上限。"""


class Budget:
    """硬边界：最大轮次、复核轮次、工具调用次数。"""

    def __init__(
        self,
        max_turns: int = 20,
        max_critic_rounds: int = 2,
        max_tool_calls: int = 40,
    ) -> None:
        self.max_turns = max_turns
        self.max_critic_rounds = max_critic_rounds
        self.max_tool_calls = max_tool_calls
        self.turns = 0
        self.critic_rounds = 0
        self.tool_calls = 0

    def spend_turn(self) -> None:
        self.turns += 1
        if self.turns > self.max_turns:
            raise BudgetExceeded(f"超出最大轮次 MAX_TURNS={self.max_turns}")

    def spend_tool_call(self) -> None:
        self.tool_calls += 1
        if self.tool_calls > self.max_tool_calls:
            raise BudgetExceeded(f"超出最大工具调用次数={self.max_tool_calls}")

    def spend_critic_round(self) -> None:
        self.critic_rounds += 1
        if self.critic_rounds > self.max_critic_rounds:
            raise BudgetExceeded(
                f"超出最大复核轮次 MAX_CRITIC_ROUNDS={self.max_critic_rounds}"
            )

    def reset(self) -> None:
        """把已用计数清零但保留上限：同一个编排实例可以跑多次任务。

        之前复用同一个 Orchestrator 跑第二次时会带着上一次的计数继续累加，
        很快就撞上 MAX_TURNS 并把任务判成 FAILED。
        """

        self.turns = 0
        self.critic_rounds = 0
        self.tool_calls = 0


class StateStore:
    """保存任务状态与检查点，支持中断后恢复。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else None
        self._memory: dict[str, dict[str, Any]] = {}

    def save(self, task_id: str, state: TaskState, payload: dict[str, Any]) -> None:
        record = {"task_id": task_id, "state": state.value, "payload": payload}
        self._memory[task_id] = record
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{task_id}.json"
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def load(self, task_id: str) -> dict[str, Any] | None:
        if task_id in self._memory:
            return self._memory[task_id]
        if self.root is not None:
            path = self.root / f"{task_id}.json"
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                self._memory[task_id] = record
                return record
        return None


def transition(current: TaskState, target: TaskState) -> TaskState:
    """校验并执行一次状态转换。"""

    if target not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(f"不允许的状态转换：{current.value} → {target.value}")
    return target


def snapshot(record: Any) -> dict[str, Any]:
    """把任务记录转成可序列化的字典。"""

    return asdict(record)
