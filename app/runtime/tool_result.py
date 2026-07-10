"""Common ToolResult type for the runtime layer.

This is a runtime-level wrapper that normalizes tool execution results
into a uniform shape for event emission and UI display. It is distinct
from app.tool.base.ToolResult, which is the tool-level return type.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Normalized result of a tool execution.

    Attributes:
        success: whether the tool completed without error.
        output: the tool's primary output (string or structured data).
        error: error message if success is False.
        metadata: tool-specific metadata (duration, tokens, etc).
        base64_image: optional screenshot/image attached to the result.
    """

    success: bool = True
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    base64_image: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def from_error(cls, error: str, **metadata) -> "ToolResult":
        """Create an error result."""
        return cls(success=False, error=error, metadata=metadata)

    @classmethod
    def from_output(cls, output: Any, **metadata) -> "ToolResult":
        """Create a success result."""
        return cls(success=True, output=output, metadata=metadata)

    def to_dict(self) -> dict:
        return self.model_dump()
