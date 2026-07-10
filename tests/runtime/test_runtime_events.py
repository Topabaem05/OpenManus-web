"""Self-check tests for app/runtime/events.py."""

import asyncio
import io
import json

import pytest

from app.runtime.events import (
    AgentEvent,
    ConsoleEventSink,
    EventSink,
    NullEventSink,
    TaskCreatedEvent,
    TaskCompletedEvent,
    TaskFailedEvent,
    ToolCallStartedEvent,
    ToolCallOutputEvent,
    EventVisibility,
)


def test_event_has_envelope_fields():
    """Every AgentEvent must have the required envelope fields."""
    evt = TaskCreatedEvent(task_id="t1", run_id="r1", title="test task")
    assert evt.task_id == "t1"
    assert evt.run_id == "r1"
    assert evt.type == "task.created"
    assert evt.event_id.startswith("evt_")
    assert evt.sequence == 0
    assert evt.timestamp  # auto-generated
    assert evt.visibility == "user" or evt.visibility == EventVisibility.USER
    assert evt.persist is True


def test_event_to_json_is_valid_json():
    """to_json() must produce parseable JSON with the type field."""
    evt = TaskCreatedEvent(task_id="t1", run_id="r1", title="hello")
    data = json.loads(evt.to_json())
    assert data["type"] == "task.created"
    assert data["task_id"] == "t1"
    assert data["payload"]["title"] == "hello"


def test_console_sink_emits_json_lines():
    """ConsoleEventSink must write one JSON line per event with sequence."""
    buf = io.StringIO()
    sink = ConsoleEventSink(stream=buf)

    async def go():
        await sink.emit(TaskCreatedEvent(task_id="t1", run_id="r1", title="a"))
        await sink.emit(ToolCallStartedEvent(task_id="t1", run_id="r1", tool="bash"))
        await sink.emit(TaskCompletedEvent(task_id="t1", run_id="r1", result="ok"))

    asyncio.run(go())

    lines = buf.getvalue().strip().split("\n")
    assert len(lines) == 3

    first = json.loads(lines[0])
    second = json.loads(lines[1])
    third = json.loads(lines[2])

    assert first["type"] == "task.created"
    assert first["sequence"] == 1
    assert second["type"] == "tool.call_started"
    assert second["sequence"] == 2
    assert third["type"] == "task.completed"
    assert third["sequence"] == 3


def test_null_sink_silently_consumes():
    """NullEventSink must not raise."""
    sink = NullEventSink()

    async def go():
        await sink.emit(TaskCreatedEvent(task_id="t1", run_id="r1"))

    asyncio.run(go())  # no exception


def test_event_sink_is_abstract():
    """EventSink must not be directly instantiable."""
    with pytest.raises(TypeError):
        EventSink()


def test_all_event_types_have_distinct_type_strings():
    """Each event subclass must declare a unique type string."""
    types = []
    for cls in [
        TaskCreatedEvent,
        ToolCallStartedEvent,
        ToolCallOutputEvent,
        TaskCompletedEvent,
        TaskFailedEvent,
    ]:
        evt = cls(task_id="t", run_id="r")
        types.append(evt.type)
    assert len(types) == len(set(types)), f"Duplicate type strings: {types}"
