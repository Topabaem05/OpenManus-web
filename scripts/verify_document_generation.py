"""Standalone verification that document_generation output_path reaches tool_result.created_files.
Run with: source .venv/bin/activate && python scripts/verify_document_generation.py
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.web import agent_runner  # noqa
from app.tool.document_generation import DocumentGeneration  # noqa


async def main():
    tool_input = {
        "title": "QA Event",
        "format": "pptx",
        "sections": [{"heading": "Slide 1", "bullets": ["test"]}],
        "output_path": "QA_Event.pptx",
    }
    tool = DocumentGeneration()
    result = await tool.execute(**tool_input)
    print("tool output:", result.output)

    created = agent_runner._extract_created_files(
        "document_generation", tool_input, str(result.output)
    )
    print("created_files:", created)
    assert "QA_Event.pptx" in created, "output_path not extracted"
    print("PASS: document_generation path is emitted as created_files")


if __name__ == "__main__":
    asyncio.run(main())
