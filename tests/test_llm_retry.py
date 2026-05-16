import httpx

from openai import APIConnectionError, NotFoundError

from app.llm import should_retry_llm_error


def test_should_not_retry_model_not_found():
    request = httpx.Request("POST", "http://example.test/v1/chat/completions")
    response = httpx.Response(404, request=request)
    error = NotFoundError("model not found", response=response, body={})

    assert should_retry_llm_error(error) is False


def test_should_retry_connection_error():
    request = httpx.Request("POST", "http://example.test/v1/chat/completions")
    error = APIConnectionError(request=request)

    assert should_retry_llm_error(error) is True
