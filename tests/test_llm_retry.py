from types import SimpleNamespace

import httpx
import pytest

from openai import APIConnectionError, NotFoundError
from openai.types.chat import ChatCompletionMessage

from app.config import LLMSettings
from app.llm import LLM, should_retry_llm_error
from app.schema import Message, ToolChoice


def test_should_not_retry_model_not_found():
    request = httpx.Request("POST", "http://example.test/v1/chat/completions")
    response = httpx.Response(404, request=request)
    error = NotFoundError("model not found", response=response, body={})

    assert should_retry_llm_error(error) is False


def test_should_retry_connection_error():
    request = httpx.Request("POST", "http://example.test/v1/chat/completions")
    error = APIConnectionError(request=request)

    assert should_retry_llm_error(error) is True


def test_openrouter_provider_options():
    settings = LLMSettings(
        api_type="openrouter",
        model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        max_tokens=1024,
        temperature=0.2,
        api_version="",
        reasoning_enabled=True,
        app_name="OpenManus-web",
        app_url="http://localhost:5173",
    )
    llm = LLM(
        config_name="openrouter-test",
        llm_config={"default": settings, "openrouter-test": settings},
    )

    assert llm._is_openrouter() is True
    assert llm._provider_headers() == {
        "X-OpenRouter-Title": "OpenManus-web",
        "HTTP-Referer": "http://localhost:5173",
    }
    assert llm._apply_provider_options({}) == {
        "extra_body": {"reasoning": {"enabled": True}}
    }


@pytest.mark.asyncio
async def test_tool_params_are_omitted_when_tool_calls_disabled():
    settings = LLMSettings(
        api_type="openrouter",
        model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        max_tokens=1024,
        temperature=0.2,
        api_version="",
        reasoning_enabled=True,
        tool_calls_enabled=False,
    )
    llm = LLM(
        config_name="openrouter-no-tools-test",
        llm_config={"default": settings, "openrouter-no-tools-test": settings},
    )

    captured = {}

    async def create(**params):
        captured.update(params)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=ChatCompletionMessage(role="assistant", content="OK")
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    response = await llm.ask_tool(
        messages=[Message.user_message("hello")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "No operation",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice=ToolChoice.AUTO,
    )

    assert response.content == "OK"
    assert "tools" not in captured
    assert "tool_choice" not in captured
    assert captured["extra_body"] == {"reasoning": {"enabled": True}}


@pytest.mark.asyncio
async def test_tool_params_are_sent_when_tool_calls_enabled():
    settings = LLMSettings(
        api_type="openrouter",
        model="deepseek/deepseek-v4-flash:free",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        max_tokens=1024,
        temperature=0.2,
        api_version="",
        reasoning_enabled=True,
        tool_calls_enabled=True,
    )
    llm = LLM(
        config_name="openrouter-tools-test",
        llm_config={"default": settings, "openrouter-tools-test": settings},
    )

    captured = {}
    tools = [
        {
            "type": "function",
            "function": {
                "name": "noop",
                "description": "No operation",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    async def create(**params):
        captured.update(params)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=ChatCompletionMessage(role="assistant", content="OK")
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    response = await llm.ask_tool(
        messages=[Message.user_message("hello")],
        tools=tools,
        tool_choice=ToolChoice.AUTO,
    )

    assert response.content == "OK"
    assert captured["tools"] == tools
    assert captured["tool_choice"] == ToolChoice.AUTO
    assert captured["extra_body"] == {"reasoning": {"enabled": True}}
