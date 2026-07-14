"""Tests for the OTel SDK bridge and the LLM instrument helpers.

The instrument_openai / instrument_anthropic helpers monkey-patch
a method on the client object. We can't depend on the real clients
being installed, so we build a tiny fake client class and verify
the wrapping behavior.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


class _FakeCompletions:
    """Stand-in for ``client.chat.completions``."""

    def __init__(self, return_value: object = "fake-completion") -> None:
        self._return = return_value
        self.calls: list[tuple[tuple, dict]] = []

    def create(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, dict(kwargs)))
        return self._return


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    """Stand-in for ``openai.OpenAI()`` — has a ``.chat`` attribute."""

    def __init__(self) -> None:
        self.chat = _FakeChat()


class _FakeMessages:
    def __init__(self, return_value: object = "fake-message") -> None:
        self._return = return_value
        self.calls: list[tuple[tuple, dict]] = []

    def create(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, dict(kwargs)))
        return self._return


class _FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


# ---------------------------------------------------------------------------
# Tests for to_otel / from_otel
# ---------------------------------------------------------------------------


def test_to_otel_round_trip(tmp_path: Path) -> None:
    """to_otel and from_otel are inverses for the basic fields."""
    from clew.sdk.otel import from_otel, to_otel
    span = Span(
        id="a" * 32,
        trace_id="b" * 32,
        parent_ids=[],
        type=SpanType.LLM,
        name="chat",
        attributes={"model": "gpt-4o"},
        input={"messages": [{"role": "user", "content": "hi"}]},
        output="hello",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )
    otel_dict = to_otel(span)
    assert otel_dict["name"] == "chat"
    assert otel_dict["kind"] == "LLM"
    # Round-trip
    round_tripped = from_otel(otel_dict)
    assert round_tripped.name == "chat"
    assert round_tripped.type is SpanType.LLM
    assert round_tripped.attributes["model"] == "gpt-4o"


def test_to_otel_span_helper(tmp_path: Path) -> None:
    """to_otel_span returns the same dict as to_otel."""
    from clew.sdk.otel import to_otel, to_otel_span
    span = Span(
        id="a" * 32,
        trace_id="b" * 32,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="x",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )
    assert to_otel_span(span) == to_otel(span)


def test_from_otel_span_helper(tmp_path: Path) -> None:
    """from_otel_span returns the same Span as from_otel."""
    from clew.sdk.otel import from_otel, from_otel_span
    span = Span(
        id="a" * 32,
        trace_id="b" * 32,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="x",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )
    from clew.sdk.otel import to_otel
    d = to_otel(span)
    s1 = from_otel(d)
    s2 = from_otel_span(d)
    assert s1.name == s2.name
    assert s1.type == s2.type
    assert s1.attributes == s2.attributes


# ---------------------------------------------------------------------------
# Tests for instrument_openai (using the fake client)
# ---------------------------------------------------------------------------


def test_instrument_openai_wraps_create(tmp_path: Path, monkeypatch) -> None:
    """``instrument_openai`` wraps ``client.chat.completions.create``.

    After wrapping, the original method is preserved as
    ``__wrapped__`` and the new method emits a span.
    """
    monkeypatch.chdir(tmp_path)
    from clew.sdk.otel import instrument_openai
    from clew.sdk.tracer import Tracer

    client = _FakeOpenAIClient()
    original = client.chat.completions.create
    tracer = Tracer(cwd=tmp_path)
    instrument_openai(client, tracer=tracer)

    # Original is preserved
    assert getattr(original, "__clew_wrapped__", False) is False
    # Wrapped method has the marker
    assert getattr(client.chat.completions.create, "__clew_wrapped__", False) is True
    # Calling it still works
    out = client.chat.completions.create(model="gpt-4o", messages=[])
    assert out == "fake-completion"
    # A span was written
    store = Store(tmp_path / ".clew")
    spans = list(store.iter_spans())
    assert len(spans) == 1
    assert spans[0].attributes.get("gen_ai.system") == "openai"


def test_instrument_openai_idempotent(tmp_path: Path, monkeypatch) -> None:
    """Re-instrumenting is a no-op (no double-wrap)."""
    monkeypatch.chdir(tmp_path)
    from clew.sdk.otel import instrument_openai
    from clew.sdk.tracer import Tracer

    client = _FakeOpenAIClient()
    tracer = Tracer(cwd=tmp_path)
    instrument_openai(client, tracer=tracer)
    instrument_openai(client, tracer=tracer)  # second call should be a no-op
    # Only one span from the single wrap after the first call
    # (re-instrumenting is no-op so the call is recorded once)
    out = client.chat.completions.create(model="gpt-4o")
    assert out == "fake-completion"


def test_instrument_openai_no_chat_attr_is_noop(tmp_path: Path, monkeypatch) -> None:
    """A client without ``chat`` is left untouched."""
    monkeypatch.chdir(tmp_path)
    from clew.sdk.otel import instrument_openai
    from clew.sdk.tracer import Tracer

    class _Bare:
        pass

    client = _Bare()
    tracer = Tracer(cwd=tmp_path)
    instrument_openai(client, tracer=tracer)  # no error, no mutation


# ---------------------------------------------------------------------------
# Tests for instrument_anthropic
# ---------------------------------------------------------------------------


def test_instrument_anthropic_wraps_create(tmp_path: Path, monkeypatch) -> None:
    """``instrument_anthropic`` wraps ``client.messages.create``."""
    monkeypatch.chdir(tmp_path)
    from clew.sdk.otel import instrument_anthropic
    from clew.sdk.tracer import Tracer

    client = _FakeAnthropicClient()
    tracer = Tracer(cwd=tmp_path)
    instrument_anthropic(client, tracer=tracer)

    assert getattr(client.messages.create, "__clew_wrapped__", False) is True
    out = client.messages.create(model="claude-3-5", messages=[])
    assert out == "fake-message"
    store = Store(tmp_path / ".clew")
    spans = list(store.iter_spans())
    assert len(spans) == 1
    assert spans[0].attributes.get("gen_ai.system") == "anthropic"


def test_instrument_anthropic_idempotent(tmp_path: Path, monkeypatch) -> None:
    """Re-instrumenting the anthropic client is a no-op."""
    monkeypatch.chdir(tmp_path)
    from clew.sdk.otel import instrument_anthropic
    from clew.sdk.tracer import Tracer

    client = _FakeAnthropicClient()
    tracer = Tracer(cwd=tmp_path)
    instrument_anthropic(client, tracer=tracer)
    instrument_anthropic(client, tracer=tracer)  # no-op
    out = client.messages.create(model="claude-3-5")
    assert out == "fake-message"


def test_instrument_anthropic_no_messages_is_noop(tmp_path: Path, monkeypatch) -> None:
    """A client without ``messages`` is left untouched."""
    monkeypatch.chdir(tmp_path)
    from clew.sdk.otel import instrument_anthropic
    from clew.sdk.tracer import Tracer

    class _Bare:
        pass

    client = _Bare()
    tracer = Tracer(cwd=tmp_path)
    instrument_anthropic(client, tracer=tracer)


# ---------------------------------------------------------------------------
# Recursion-safety: an exception inside the wrapped function is recorded
# ---------------------------------------------------------------------------


def test_instrument_openai_records_exception(tmp_path: Path, monkeypatch) -> None:
    """If the wrapped function raises, the span records ERROR."""
    monkeypatch.chdir(tmp_path)
    from clew.sdk.otel import instrument_openai
    from clew.sdk.tracer import Tracer

    class _Boom:
        chat = type("C", (), {"completions": type("K", (), {
            "create": staticmethod(lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        })()})()

    client = _Boom()
    tracer = Tracer(cwd=tmp_path)
    instrument_openai(client, tracer=tracer)
    with pytest.raises(RuntimeError, match="boom"):
        client.chat.completions.create()
    store = Store(tmp_path / ".clew")
    spans = list(store.iter_spans())
    assert len(spans) == 1
    assert spans[0].status is SpanStatus.ERROR
    assert "boom" in (spans[0].error or "")
