"""Self-check tests for app/runtime/tool_result.py."""

from app.runtime.tool_result import ToolResult


def test_success_result():
    r = ToolResult(success=True, output="ok")
    assert r.success is True
    assert r.output == "ok"
    assert r.error is None


def test_from_output_factory():
    r = ToolResult.from_output("hello", duration=1.5)
    assert r.success is True
    assert r.output == "hello"
    assert r.metadata["duration"] == 1.5


def test_from_error_factory():
    r = ToolResult.from_error("boom", code=500)
    assert r.success is False
    assert r.error == "boom"
    assert r.metadata["code"] == 500


def test_to_dict():
    r = ToolResult(success=True, output="data", base64_image="img")
    d = r.to_dict()
    assert d["success"] is True
    assert d["output"] == "data"
    assert d["base64_image"] == "img"
