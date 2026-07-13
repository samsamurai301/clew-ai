"""Tests for clew.core.format — to_otel / from_otel roundtrip and shape."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clew.core.format import from_otel, to_otel
from clew.core.models import Span, SpanStatus, SpanType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_span(**overrides: object) -> Span:
    defaults: dict[str, object] = {
        "id": "1" * 64,
        "trace_id": "2" * 64,
        "parent_ids": ["3" * 64],
        "type": SpanType.LLM,
        "name": "chat",
        "attributes": {"gen_ai.system": "openai", "gen_ai.request.model": "gpt-4o"},
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        "output": {"message": {"role": "assistant", "content": "hello"}},
        "started_at": datetime(2026, 7, 13, 18, 0, 0, tzinfo=UTC),
        "ended_at": datetime(2026, 7, 13, 18, 0, 1, tzinfo=UTC),
        "status": SpanStatus.OK,
    }
    defaults.update(overrides)
    return Span(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Basic roundtrip
# ---------------------------------------------------------------------------


def test_to_otel_roundtrip() -> None:
    """from_otel(to_otel(span)) equals the original span (modulo id)."""
    span = _make_span()
    otel = to_otel(span)
    reconstructed = from_otel(otel)
    assert reconstructed == span


def test_to_otel_roundtrip_error_status() -> None:
    """Error status and message survive a roundtrip."""
    span = _make_span(status=SpanStatus.ERROR, error="boom")
    # Span model requires non-empty error for ERROR status.
    otel = to_otel(span)
    assert otel["status"]["code"] == "ERROR"
    assert otel["status"]["message"] == "boom"
    reconstructed = from_otel(otel)
    assert reconstructed.status is SpanStatus.ERROR
    assert reconstructed.error == "boom"


def test_to_otel_contains_gen_ai_attributes() -> None:
    """to_otel preserves the gen_ai.* attributes verbatim."""
    span = _make_span(attributes={"gen_ai.system": "anthropic", "gen_ai.request.model": "claude"})
    otel = to_otel(span)
    assert otel["attributes"]["gen_ai.system"] == "anthropic"
    assert otel["attributes"]["gen_ai.request.model"] == "claude"


def test_to_otel_emits_canonical_time() -> None:
    """to_otel emits Z-suffixed ISO 8601 strings."""
    span = _make_span()
    otel = to_otel(span)
    assert otel["start_time"] == "2026-07-13T18:00:00Z"
    assert otel["end_time"] == "2026-07-13T18:00:01Z"


def test_to_otel_includes_kind() -> None:
    """The 'kind' field carries the SpanType string."""
    span = _make_span(type=SpanType.TOOL)
    otel = to_otel(span)
    assert otel["kind"] == "TOOL"


# ---------------------------------------------------------------------------
# Realistic OpenAI completion span
# ---------------------------------------------------------------------------


def test_openai_completion_span_roundtrip() -> None:
    """A real-looking OpenAI completion span round-trips losslessly."""
    otel = {
        "name": "plan_step_1",
        "kind": "LLM",
        "start_time": "2026-07-13T18:28:55.123Z",
        "end_time": "2026-07-13T18:28:56.789Z",
        "status": {"code": "OK", "message": ""},
        "attributes": {
            "gen_ai.system": "openai",
            "gen_ai.request.model": "gpt-4o-2024-08-06",
            "gen_ai.request.temperature": 0.2,
            "gen_ai.request.max_tokens": 1024,
            "gen_ai.response.model": "gpt-4o-2024-08-06",
            "gen_ai.response.finish_reason": "stop",
            "gen_ai.usage.input_tokens": 612,
            "gen_ai.usage.output_tokens": 87,
            "gen_ai.completion": "The population of Iceland is approximately 400,000.",
        },
        "trace_id": "a" * 64,
        "span_id": "b" * 64,
        "parent_span_id": "c" * 64,
        "input": {
            "messages": [
                {"role": "system", "content": "You are a research assistant."},
                {"role": "user", "content": "What is the population of Iceland?"},
            ],
        },
        "output": {
            "message": {
                "role": "assistant",
                "content": "The population of Iceland is approximately 400,000.",
            }
        },
    }
    span = from_otel(otel)
    assert span.name == "plan_step_1"
    assert span.type is SpanType.LLM
    assert span.attributes["gen_ai.system"] == "openai"
    assert span.attributes["gen_ai.request.model"] == "gpt-4o-2024-08-06"
    assert span.attributes["gen_ai.usage.input_tokens"] == 612
    assert span.attributes["gen_ai.usage.output_tokens"] == 87
    assert span.attributes["gen_ai.response.finish_reason"] == "stop"
    assert span.parent_ids == ["c" * 64]
    assert span.trace_id == "a" * 64
    assert span.id == "b" * 64

    # Round-trip back to OTel and check stability.
    otel2 = to_otel(span)
    assert otel2["name"] == otel["name"]
    assert otel2["kind"] == otel["kind"]
    assert otel2["attributes"]["gen_ai.system"] == "openai"
    assert otel2["attributes"]["gen_ai.usage.input_tokens"] == 612
    assert otel2["input"] == otel["input"]
    assert otel2["output"] == otel["output"]
    assert otel2["start_time"] == otel["start_time"]
    assert otel2["end_time"] == otel["end_time"]
    # And the double roundtrip is stable.
    span2 = from_otel(otel2)
    assert span2 == span


# ---------------------------------------------------------------------------
# Tool call span
# ---------------------------------------------------------------------------


def test_tool_call_span_roundtrip() -> None:
    """A real-looking tool call span round-trips losslessly."""
    otel = {
        "name": "search_web",
        "kind": "TOOL",
        "start_time": "2026-07-13T18:28:56.812Z",
        "end_time": "2026-07-13T18:28:57.440Z",
        "status": {"code": "OK", "message": ""},
        "attributes": {
            "gen_ai.tool.name": "search_web",
            "gen_ai.tool.call.id": "call_AbCdEf123",
            "gen_ai.tool.call.arguments": '{"query": "population of Iceland 2026"}',
            "http.request.method": "GET",
            "url.full": "https://duckduckgo.com/html/?q=population+of+Iceland+2026",
        },
        "trace_id": "a" * 64,
        "span_id": "d" * 64,
        "parent_span_id": "b" * 64,
        "input": {"query": "population of Iceland 2026", "max_results": 5},
        "output": {
            "results": [
                {
                    "title": "Iceland — Population",
                    "url": "https://www.worldometers.info/world-population/iceland-population/",
                    "snippet": "Iceland 2026 population is estimated at 399,182 people.",
                }
            ]
        },
    }
    span = from_otel(otel)
    assert span.name == "search_web"
    assert span.type is SpanType.TOOL
    assert span.attributes["gen_ai.tool.name"] == "search_web"
    assert span.attributes["gen_ai.tool.call.id"] == "call_AbCdEf123"
    assert span.attributes["gen_ai.tool.call.arguments"] == (
        '{"query": "population of Iceland 2026"}'
    )

    # Round-trip back and check stability.
    otel2 = to_otel(span)
    assert otel2["name"] == "search_web"
    assert otel2["kind"] == "TOOL"
    assert otel2["attributes"]["gen_ai.tool.name"] == "search_web"
    assert otel2["attributes"]["gen_ai.tool.call.id"] == "call_AbCdEf123"
    assert otel2["input"] == otel["input"]
    assert otel2["output"] == otel["output"]
    span2 = from_otel(otel2)
    assert span2 == span


# ---------------------------------------------------------------------------
# from_otel type inference
# ---------------------------------------------------------------------------


def test_from_otel_infers_type_from_attributes() -> None:
    """When 'kind' is missing, type is inferred from gen_ai.* attributes."""
    otel = {
        "name": "chat",
        "start_time": "2026-07-13T18:28:55.000Z",
        "end_time": "2026-07-13T18:28:56.000Z",
        "attributes": {
            "gen_ai.system": "openai",
            "gen_ai.request.model": "gpt-4o",
        },
        "trace_id": "a" * 64,
        "span_id": "b" * 64,
    }
    span = from_otel(otel)
    assert span.type is SpanType.LLM


def test_from_otel_infers_tool_from_attributes() -> None:
    """Tool attributes imply SpanType.TOOL."""
    otel = {
        "name": "search_web",
        "start_time": "2026-07-13T18:28:55.000Z",
        "end_time": "2026-07-13T18:28:56.000Z",
        "attributes": {
            "gen_ai.tool.name": "search_web",
            "gen_ai.tool.call.id": "call_1",
        },
        "trace_id": "a" * 64,
        "span_id": "b" * 64,
    }
    span = from_otel(otel)
    assert span.type is SpanType.TOOL


def test_from_otel_observation_default() -> None:
    """An attribute-less span with no kind defaults to OBSERVATION."""
    otel = {
        "name": "tick",
        "start_time": "2026-07-13T18:28:55.000Z",
        "end_time": "2026-07-13T18:28:56.000Z",
        "trace_id": "a" * 64,
        "span_id": "b" * 64,
    }
    span = from_otel(otel)
    assert span.type is SpanType.OBSERVATION


def test_from_otel_requires_start_time() -> None:
    """from_otel raises ValueError if no time field is present."""
    with pytest.raises(ValueError, match="start_time|started_at"):
        from_otel({"name": "x", "trace_id": "a" * 64, "span_id": "b" * 64})


def test_from_otel_error_status_with_message() -> None:
    """An ERROR status with a message is preserved."""
    otel = {
        "name": "x",
        "kind": "LLM",
        "start_time": "2026-07-13T18:28:55.000Z",
        "end_time": "2026-07-13T18:28:56.000Z",
        "status": {"code": "ERROR", "message": "rate limited"},
        "attributes": {},
        "trace_id": "a" * 64,
        "span_id": "b" * 64,
    }
    span = from_otel(otel)
    assert span.status is SpanStatus.ERROR
    assert span.error == "rate limited"


def test_from_otel_error_status_without_message() -> None:
    """An ERROR status without a message gets a placeholder error."""
    otel = {
        "name": "x",
        "kind": "LLM",
        "start_time": "2026-07-13T18:28:55.000Z",
        "end_time": "2026-07-13T18:28:56.000Z",
        "status": {"code": "ERROR", "message": ""},
        "attributes": {},
        "trace_id": "a" * 64,
        "span_id": "b" * 64,
    }
    span = from_otel(otel)
    assert span.status is SpanStatus.ERROR
    assert span.error  # non-empty


def test_from_otel_handles_z_suffix() -> None:
    """from_otel accepts the Z-suffixed ISO 8601 form."""
    otel = {
        "name": "x",
        "start_time": "2026-07-13T18:28:55.123Z",
        "end_time": "2026-07-13T18:28:55.123Z",
        "trace_id": "a" * 64,
        "span_id": "b" * 64,
    }
    span = from_otel(otel)
    assert span.started_at.tzinfo is not None
    assert span.started_at.year == 2026
    assert span.started_at.microsecond == 123000
