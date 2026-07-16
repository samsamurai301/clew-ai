"""Tests for clew.sdk.otel (OpenAI / Anthropic auto-instrumentation)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any, ClassVar

import pytest

from clew.core.models import SpanStatus, SpanType
from clew.sdk.otel import instrument_anthropic, instrument_openai
from clew.sdk.tracer import Tracer


class _FakeCompletions:
    """A minimal stub mimicking the OpenAI completions API."""

    def __init__(self) -> None:
        self.create = self._create

    def _create(self, *args: Any, **kwargs: Any) -> Any:
        # Return a minimal object that looks like an OpenAI response.
        class _Choice:
            message = type("M", (), {"content": "hello from gpt"})()

        class _Response:
            choices: ClassVar = [_Choice()]

        return _Response()


class _FakeMessages:
    def __init__(self) -> None:
        self.create = self._create

    def _create(self, *args: Any, **kwargs: Any) -> Any:
        class _Block:
            text = "hi from claude"
            type = "text"

        class _Content:
            def __init__(self) -> None:
                self.content = [_Block()]

        return _Content()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


class _FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


class _AsyncOpenAIClient:
    def __init__(self, *, fail: bool = False) -> None:
        class _Usage:
            prompt_tokens = 11
            completion_tokens = 7

        class _Choice:
            message = type("Message", (), {"content": "async gpt"})()

        class _Response:
            choices: ClassVar = [_Choice()]
            usage = _Usage()

        class _Completions:
            async def create(self, *args: Any, **kwargs: Any) -> Any:
                if fail:
                    raise RuntimeError("async provider failure")
                return _Response()

        self.chat = type("Chat", (), {"completions": _Completions()})()


class _AsyncAnthropicClient:
    def __init__(self) -> None:
        class _Usage:
            input_tokens = 5
            output_tokens = 3

        class _Block:
            type = "text"
            text = "async claude"

        class _Response:
            content: ClassVar = [_Block()]
            usage = _Usage()

        class _Messages:
            async def create(self, *args: Any, **kwargs: Any) -> Any:
                return _Response()

        self.messages = _Messages()


def test_instrument_openai_records_span(tmp_path: Path) -> None:
    """instrument_openai wraps client.chat.completions.create as a span."""
    t = Tracer(cwd=tmp_path)
    client = _FakeOpenAIClient()
    instrument_openai(client, tracer=t)
    # Calling the wrapped method should produce a span.
    response = client.chat.completions.create(model="gpt-4o", messages=[])
    assert response.choices[0].message.content == "hello from gpt"
    # Inspect the store.
    spans = list(t.store.store.iter_spans())
    names = {s.name for s in spans}
    assert "openai.chat.completions.create" in names
    span = next(s for s in spans if s.name == "openai.chat.completions.create")
    assert span.type.name == "LLM"
    assert span.output == "hello from gpt"


def test_instrument_openai_idempotent(tmp_path: Path) -> None:
    """Instrumenting the same client twice does not double-wrap."""
    t = Tracer(cwd=tmp_path)
    client = _FakeOpenAIClient()
    instrument_openai(client, tracer=t)
    original = client.chat.completions.create
    instrument_openai(client, tracer=t)  # second call should be a no-op
    assert client.chat.completions.create is original


def test_instrument_anthropic_records_span(tmp_path: Path) -> None:
    """instrument_anthropic wraps client.messages.create as a span."""
    t = Tracer(cwd=tmp_path)
    client = _FakeAnthropicClient()
    instrument_anthropic(client, tracer=t)
    response = client.messages.create(model="claude-3", max_tokens=10, messages=[])
    assert response.content[0].text == "hi from claude"
    spans = list(t.store.store.iter_spans())
    names = {s.name for s in spans}
    assert "anthropic.messages.create" in names
    span = next(s for s in spans if s.name == "anthropic.messages.create")
    assert span.type.name == "LLM"
    assert span.output == "hi from claude"


def test_instrument_anthropic_idempotent(tmp_path: Path) -> None:
    t = Tracer(cwd=tmp_path)
    client = _FakeAnthropicClient()
    instrument_anthropic(client, tracer=t)
    original = client.messages.create
    instrument_anthropic(client, tracer=t)
    assert client.messages.create is original


def test_instrument_openai_records_exception(tmp_path: Path) -> None:
    """An exception in the wrapped call is captured as SpanStatus.ERROR."""
    t = Tracer(cwd=tmp_path)
    client = _FakeOpenAIClient()
    instrument_openai(client, tracer=t)

    # Replace the wrapped create with a function that raises.
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("rate limit")

    client.chat.completions.create = boom  # type: ignore[assignment]
    # Re-instrument to wrap the new function.
    instrument_openai(client, tracer=t)
    with contextlib.suppress(RuntimeError):
        client.chat.completions.create()
    spans = list(t.store.store.iter_spans())
    span = next(s for s in spans if "create" in s.name)
    assert span.status.name == "ERROR"
    assert "rate limit" in (span.error or "")


def test_instrument_openai_async_captures_usage_redacts_and_nests(
    tmp_path: Path,
) -> None:
    """Async calls retain the parent and capture safe provider details."""
    t = Tracer(cwd=tmp_path)
    client = _AsyncOpenAIClient()
    instrument_openai(client, tracer=t)

    @t.agent
    async def agent() -> str:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            api_key="must-not-persist",
        )
        return response.choices[0].message.content

    assert asyncio.run(agent()) == "async gpt"
    spans = list(t.store.store.iter_spans())
    outer = next(span for span in spans if span.name == "agent")
    provider = next(span for span in spans if span.type is SpanType.LLM)
    assert provider.parent_ids == [outer.id]
    assert provider.output == "async gpt"
    assert provider.input["kwargs"]["api_key"] == "[REDACTED]"
    assert provider.attributes["gen_ai.usage.input_tokens"] == 11
    assert provider.attributes["gen_ai.usage.output_tokens"] == 7


def test_instrument_anthropic_async_captures_output_and_usage(tmp_path: Path) -> None:
    t = Tracer(cwd=tmp_path)
    client = _AsyncAnthropicClient()
    instrument_anthropic(client, tracer=t)

    response = asyncio.run(client.messages.create(model="claude", max_tokens=20, messages=[]))
    assert response.content[0].text == "async claude"
    span = next(t.store.store.iter_spans())
    assert span.output == "async claude"
    assert span.attributes["gen_ai.usage.input_tokens"] == 5
    assert span.attributes["gen_ai.usage.output_tokens"] == 3


def test_instrument_openai_async_records_errors(tmp_path: Path) -> None:
    t = Tracer(cwd=tmp_path)
    client = _AsyncOpenAIClient(fail=True)
    instrument_openai(client, tracer=t)

    with pytest.raises(RuntimeError, match="async provider failure"):
        asyncio.run(client.chat.completions.create(model="gpt-4o"))

    span = next(t.store.store.iter_spans())
    assert span.status is SpanStatus.ERROR
    assert "async provider failure" in (span.error or "")
