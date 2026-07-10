"""Per-task workspace isolation.

Each task gets its own subdirectory under the configured workspace root.
This keeps file artifacts from different tasks separate without needing
full container isolation (Phase 1 only; Docker sandbox comes later).
"""

import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from app.config import config


class TaskWorkspace:
    """Manages a per-task subdirectory under the workspace root.

    Args:
        task_id: unique task identifier. If None, a UUID is generated.
        root: override the workspace root (default: config.workspace_root).
    """

    def __init__(self, task_id: Optional[str] = None, root: Optional[str] = None):
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        self.root = Path(root or config.workspace_root)
        self.path = self.root / self.task_id

    def create(self) -> str:
        """Create the task workspace directory. Returns the path."""
        self.path.mkdir(parents=True, exist_ok=True)
        return str(self.path)

    def destroy(self) -> None:
        """Remove the task workspace directory."""
        if self.path.exists():
            shutil.rmtree(self.path)

    def resolve(self, *parts: str) -> str:
        """Resolve a relative path within the task workspace."""
        return str(self.path / Path(*parts))

    def exists(self) -> bool:
        return self.path.exists()

    def list_files(self) -> list:
        """List files in the workspace (non-recursive)."""
        if not self.path.exists():
            return []
        return [str(p.relative_to(self.path)) for p in self.path.iterdir()]
