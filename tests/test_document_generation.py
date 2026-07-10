"""Tests for document_generation tool and VM runner integration."""
import asyncio
from pathlib import Path

import pytest

from app.tool.document_generation import DocumentGeneration
from app.web import agent_runner


@pytest.mark.asyncio
async def test_document_generation_pptx(tmp_path):
    tool = DocumentGeneration()
    result = await tool.execute(
        title="QA Deck",
        format="pptx",
        sections=[
            {"heading": "Overview", "bullets": ["Feature parity", "PPT generation"]},
            {"heading": "Next", "bullets": ["UI polish", "VM QA"]},
        ],
        output_path=str(tmp_path / "QA_Deck.pptx"),
    )
    assert result.error is None
    assert "PowerPoint deck saved" in result.output
    assert Path(tmp_path / "QA_Deck.pptx").exists()


@pytest.mark.asyncio
async def test_document_generation_docx(tmp_path):
    tool = DocumentGeneration()
    result = await tool.execute(
        title="QA Doc",
        format="docx",
        sections=[
            {"heading": "Intro", "bullets": ["text"]},
        ],
        output_path=str(tmp_path / "QA_Doc.docx"),
    )
    assert result.error is None
    assert "Word document saved" in result.output
    assert Path(tmp_path / "QA_Doc.docx").exists()


def test_extract_created_files_for_document_generation():
    tool_input = {
        "title": "QA Event",
        "format": "pptx",
        "sections": [{"heading": "Slide 1", "bullets": ["test"]}],
        "output_path": "QA_Event.pptx",
    }
    created = agent_runner._extract_created_files(
        "document_generation", tool_input, "PowerPoint deck saved to QA_Event.pptx"
    )
    assert created == ["QA_Event.pptx"]


def test_extract_created_files_without_output_path():
    created = agent_runner._extract_created_files(
        "document_generation", {"title": "x", "format": "pptx"}, ""
    )
    assert created == []
