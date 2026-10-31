"""供应商超长错误统一包装，其他 HTTP 错误保持原类型。"""

from unittest.mock import AsyncMock

import anthropic
import httpx2
import openai
import pytest

from termcode.config import ProviderConfig
from termcode.llm import Message, PromptTooLongError, Request, new_provider


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
@pytest.mark.parametrize("kind", ["ptl", "ordinary400", "server500"])
async def test_provider_wraps_only_prompt_too_long(protocol, kind, monkeypatch):
    sdk = anthropic if protocol == "anthropic" else openai
    response = httpx2.Response(
        500 if kind == "server500" else 400,
        request=httpx2.Request("POST", "https://example.test"),
    )
    error_type = sdk.InternalServerError if kind == "server500" else sdk.BadRequestError
    body = {"code": "context_length_exceeded" if kind == "ptl" else "invalid_model"}
    error = error_type(
        "prompt is too long" if kind == "ptl" else "invalid request",
        response=response,
        body=body,
    )
    provider = new_provider(
        ProviderConfig("fake", protocol, "TEST_SECRET", "test-model")
    )
    if protocol == "anthropic":

        def fail(**kwargs):
            raise error

        monkeypatch.setattr(provider._client.messages, "stream", fail)
    else:
        monkeypatch.setattr(
            provider._client.chat.completions, "create", AsyncMock(side_effect=error)
        )
    try:
        events = [e async for e in provider.stream(Request([Message("user", "hello")]))]
    finally:
        await provider.aclose()
    assert len(events) == 1 and not events[0].done
    if kind == "ptl":
        assert isinstance(events[0].err, PromptTooLongError)
        assert events[0].err.__cause__ is error
    else:
        assert events[0].err is error
