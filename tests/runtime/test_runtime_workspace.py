"""Self-check tests for app/runtime/workspace.py."""

import tempfile

from app.runtime.workspace import TaskWorkspace


def test_workspace_creates_dir():
    with tempfile.TemporaryDirectory() as tmp:
        ws = TaskWorkspace(task_id="test_ws_1", root=tmp)
        path = ws.create()
        assert ws.exists()
        assert "test_ws_1" in path


def test_workspace_destroys_dir():
    with tempfile.TemporaryDirectory() as tmp:
        ws = TaskWorkspace(task_id="test_ws_2", root=tmp)
        ws.create()
        assert ws.exists()
        ws.destroy()
        assert not ws.exists()


def test_workspace_list_files():
    with tempfile.TemporaryDirectory() as tmp:
        ws = TaskWorkspace(task_id="test_ws_3", root=tmp)
        ws.create()
        (ws.path / "a.txt").write_text("hello")
        (ws.path / "b.txt").write_text("world")
        files = ws.list_files()
        assert "a.txt" in files
        assert "b.txt" in files


def test_workspace_resolve_path():
    with tempfile.TemporaryDirectory() as tmp:
        ws = TaskWorkspace(task_id="test_ws_4", root=tmp)
        ws.create()
        resolved = ws.resolve("subdir", "file.txt")
        assert "test_ws_4" in resolved
        assert resolved.endswith("subdir/file.txt") or resolved.endswith("subdir\\file.txt")
