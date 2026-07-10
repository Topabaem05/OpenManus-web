"""Structured agent event system.

Every agent action emits a typed AgentEvent. Events flow through an
EventSink, which can persist, stream, or print them. The in-memory
TaskRunner assigns monotonically increasing sequence numbers.
"""

import json
import sys
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class EventVisibility(str, Enum):
    """Who can see an event."""

    USER = "user"
    INTERNAL = "internal"
    HIDDEN = "hidden"


class AgentEvent(BaseModel):
    """Base envelope for all agent events.

    All events share this envelope: a unique id, the task and run they
    belong to, a monotonically increasing sequence, a type discriminator,
    a timestamp, an arbitrary payload, visibility, and whether to persist.
    """

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:24]}")
    task_id: str
    run_id: str
    sequence: int = 0
    type: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    payload: Dict[str, Any] = Field(default_factory=dict)
    visibility: EventVisibility = EventVisibility.USER
    persist: bool = True

    class Config:
        use_enum_values = True

    def to_json(self) -> str:
        """Serialize to a single JSON line."""
        return json.dumps(self.model_dump(), default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Concrete event types
# ---------------------------------------------------------------------------


class TaskCreatedEvent(AgentEvent):
    type: str = "task.created"

    def __init__(self, task_id: str, run_id: str, title: str = "", **kw):
        super().__init__(
            task_id=task_id, run_id=run_id, payload={"title": title, **kw}, **{}
        )


class TaskStatusChangedEvent(AgentEvent):
    type: str = "task.status_changed"

    def __init__(self, task_id: str, run_id: str, status: str, **kw):
        super().__init__(
            task_id=task_id, run_id=run_id, payload={"status": status, **kw}, **{}
        )


class PlanCreatedEvent(AgentEvent):
    type: str = "plan.created"

    def __init__(self, task_id: str, run_id: str, nodes: list, **kw):
        super().__init__(
            task_id=task_id, run_id=run_id, payload={"nodes": nodes, **kw}, **{}
        )


class PlanNodeStartedEvent(AgentEvent):
    type: str = "plan.node_started"

    def __init__(self, task_id: str, run_id: str, node_id: str, title: str = "", **kw):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={"node_id": node_id, "title": title, **kw},
            **{},
        )


class ModelRequestStartedEvent(AgentEvent):
    type: str = "model.request_started"

    def __init__(
        self,
        task_id: str,
        run_id: str,
        model: str = "",
        step: int = 0,
        **kw,
    ):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={"model": model, "step": step, **kw},
            **{},
        )


class ToolCallRequestedEvent(AgentEvent):
    type: str = "tool.call_requested"

    def __init__(
        self, task_id: str, run_id: str, tool: str = "", arguments: dict = None, **kw
    ):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={"tool": tool, "arguments": arguments or {}, **kw},
            **{},
        )


class ToolCallStartedEvent(AgentEvent):
    type: str = "tool.call_started"

    def __init__(self, task_id: str, run_id: str, tool: str = "", **kw):
        super().__init__(
            task_id=task_id, run_id=run_id, payload={"tool": tool, **kw}, **{}
        )


class ToolCallOutputEvent(AgentEvent):
    type: str = "tool.call_output"

    def __init__(
        self,
        task_id: str,
        run_id: str,
        tool: str = "",
        output: str = "",
        error: Optional[str] = None,
        **kw,
    ):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={
                "tool": tool,
                "output": output,
                "error": error,
                **kw,
            },
            **{},
        )


class FileCreatedEvent(AgentEvent):
    type: str = "file.created"

    def __init__(self, task_id: str, run_id: str, path: str = "", **kw):
        super().__init__(
            task_id=task_id, run_id=run_id, payload={"path": path, **kw}, **{}
        )


class FileUpdatedEvent(AgentEvent):
    type: str = "file.updated"

    def __init__(self, task_id: str, run_id: str, path: str = "", **kw):
        super().__init__(
            task_id=task_id, run_id=run_id, payload={"path": path, **kw}, **{}
        )


class ApprovalRequestedEvent(AgentEvent):
    type: str = "approval.requested"

    def __init__(
        self,
        task_id: str,
        run_id: str,
        action: str = "",
        risk_level: str = "low",
        **kw,
    ):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={"action": action, "risk_level": risk_level, **kw},
            **{},
        )


class ApprovalResolvedEvent(AgentEvent):
    type: str = "approval.resolved"

    def __init__(
        self, task_id: str, run_id: str, approved: bool = False, **kw
    ):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={"approved": approved, **kw},
            **{},
        )


class NodeCompletedEvent(AgentEvent):
    type: str = "node.completed"

    def __init__(
        self, task_id: str, run_id: str, node_id: str = "", success: bool = True, **kw
    ):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={"node_id": node_id, "success": success, **kw},
            **{},
        )


class TaskCompletedEvent(AgentEvent):
    type: str = "task.completed"

    def __init__(
        self, task_id: str, run_id: str, result: str = "", artifacts: list = None, **kw
    ):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={"result": result, "artifacts": artifacts or [], **kw},
            **{},
        )


class TaskFailedEvent(AgentEvent):
    type: str = "task.failed"

    def __init__(
        self, task_id: str, run_id: str, error: str = "", error_code: str = "", **kw
    ):
        super().__init__(
            task_id=task_id,
            run_id=run_id,
            payload={"error": error, "error_code": error_code, **kw},
            **{},
        )


# ---------------------------------------------------------------------------
# EventSink protocol
# ---------------------------------------------------------------------------


class EventSink(ABC):
    """Protocol for consuming agent events.

    Implementations may persist to DB, stream over WebSocket, or print
    to stdout (ConsoleEventSink). The runner calls emit() for every event.
    """

    @abstractmethod
    async def emit(self, event: AgentEvent) -> None:
        """Process a single event."""


class NullEventSink(EventSink):
    """Drop-all sink for testing."""

    async def emit(self, event: AgentEvent) -> None:
        pass


class ConsoleEventSink(EventSink):
    """Print each event as a JSON line to a stream (default stdout)."""

    def __init__(self, stream=None):
        self._stream = stream or sys.stdout
        self._sequence = 0

    async def emit(self, event: AgentEvent) -> None:
        self._sequence += 1
        event.sequence = self._sequence
        self._stream.write(event.to_json() + "\n")
        self._stream.flush()
