"""Tests for the LangChain callback handler (clew.integrations.langchain)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from clew.integrations.langchain import ClewCallbackHandler
from clew.sdk.tracer import Tracer


def _make_handler(tmp_path: Path) -> tuple[ClewCallbackHandler, Tracer]:
    t = Tracer(cwd=tmp_path)
    return ClewCallbackHandler(tracer=t), t


def test_chain_start_end(tmp_path: Path) -> None:
    """on_chain_start + on_chain_end writes a span."""
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_chain_start(
        {"name": "MyChain"},
        {"input": "x"},
        run_id=run_id,
    )
    cb.on_chain_end({"output": "y"}, run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert len(spans) == 1
    assert spans[0].name == "MyChain"
    assert spans[0].type.name == "OBSERVATION"
    assert spans[0].status.name == "OK"


def test_llm_classified_as_llm(tmp_path: Path) -> None:
    """An LLM runnable becomes a SpanType.LLM span."""
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_llm_start(
        {"name": "ChatOpenAI"},
        ["hello"],
        run_id=run_id,
    )
    class _Gen:
        text = "hi back"
    class _Result:
        generations: ClassVar = [[_Gen()]]
    cb.on_llm_end(_Result(), run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert len(spans) == 1
    assert spans[0].name == "ChatOpenAI"
    assert spans[0].type.name == "LLM"
    assert spans[0].output == "hi back"


def test_tool_classified_as_tool(tmp_path: Path) -> None:
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_tool_start(
        {"name": "Search"},
        "query",
        run_id=run_id,
    )
    cb.on_tool_end("results", run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert len(spans) == 1
    assert spans[0].name == "Search"
    assert spans[0].type.name == "TOOL"


def test_chain_error(tmp_path: Path) -> None:
    """An error in the chain becomes SpanStatus.ERROR."""
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_chain_start({"name": "WillFail"}, {}, run_id=run_id)
    cb.on_chain_error(ValueError("boom"), run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert len(spans) == 1
    assert spans[0].status.name == "ERROR"
    assert "boom" in (spans[0].error or "")


def test_nested_chain(tmp_path: Path) -> None:
    """A child chain is parented onto the parent chain."""
    cb, t = _make_handler(tmp_path)
    parent = uuid4()
    child = uuid4()
    cb.on_chain_start({"name": "Outer"}, {}, run_id=parent)
    cb.on_chain_start(
        {"name": "Inner"},
        {},
        run_id=child,
        parent_run_id=parent,
    )
    cb.on_chain_end({}, run_id=child)
    cb.on_chain_end({}, run_id=parent)
    spans = list(t._store.store.iter_spans())
    by_name = {s.name: s for s in spans}
    assert by_name["Inner"].parent_ids == [by_name["Outer"].id]


def test_llm_error(tmp_path: Path) -> None:
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_llm_start({"name": "ChatOpenAI"}, ["x"], run_id=run_id)
    cb.on_llm_error(RuntimeError("rate limit"), run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert spans[0].status.name == "ERROR"
    assert "rate limit" in (spans[0].error or "")


def test_tool_error(tmp_path: Path) -> None:
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_tool_start({"name": "Search"}, "q", run_id=run_id)
    cb.on_tool_error(KeyError("missing"), run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert spans[0].status.name == "ERROR"
    assert "missing" in (spans[0].error or "")


def test_unknown_serialized_name_defaults_to_observation(tmp_path: Path) -> None:
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_chain_start({"name": "WeirdThing"}, {}, run_id=run_id)
    cb.on_chain_end({}, run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert spans[0].type.name == "OBSERVATION"


def test_no_serialized_payload(tmp_path: Path) -> None:
    """A None serialized payload still works."""
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_chain_start(None, {}, run_id=run_id)
    cb.on_chain_end({}, run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert spans[0].name == "chain"  # default


def test_close_on_missing_run_id_is_safe(tmp_path: Path) -> None:
    """Closing a span we never opened is a no-op (defensive)."""
    cb, _ = _make_handler(tmp_path)
    cb.on_chain_end({}, run_id=uuid4())  # no start; should not raise


# ---------------------------------------------------------------------------
# Error path coverage
# ---------------------------------------------------------------------------


def test_chain_error_writes_error_span(tmp_path: Path) -> None:
    """on_chain_error marks the span as ERROR with the message."""
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_chain_start(
        {"name": "FailChain"},
        {"input": "x"},
        run_id=run_id,
    )
    cb.on_chain_error(RuntimeError("boom"), run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert len(spans) == 1
    assert spans[0].name == "FailChain"
    assert spans[0].status.value == "ERROR"
    assert "boom" in (spans[0].error or "")


def test_chain_end_with_no_start_is_noop(tmp_path: Path) -> None:
    """on_chain_end for an unknown run_id is silently ignored."""
    cb, t = _make_handler(tmp_path)
    cb.on_chain_end({"output": "y"}, run_id=uuid4())
    spans = list(t._store.store.iter_spans())
    assert spans == []


def test_chain_error_with_no_start_is_noop(tmp_path: Path) -> None:
    """on_chain_error for an unknown run_id is silently ignored."""
    cb, t = _make_handler(tmp_path)
    cb.on_chain_error(RuntimeError("x"), run_id=uuid4())
    spans = list(t._store.store.iter_spans())
    assert spans == []


def test_llm_error_writes_error_span(tmp_path: Path) -> None:
    """on_llm_error marks the LLM span as ERROR."""
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_llm_start(
        {"name": "OpenAI"},
        ["prompt"],
        run_id=run_id,
    )
    cb.on_llm_error(RuntimeError("rate-limited"), run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert len(spans) == 1
    assert spans[0].name == "OpenAI"
    assert spans[0].status.value == "ERROR"
    assert "rate-limited" in (spans[0].error or "")


def test_llm_error_with_no_start_is_noop(tmp_path: Path) -> None:
    """on_llm_error for an unknown run_id is silently ignored."""
    cb, t = _make_handler(tmp_path)
    cb.on_llm_error(RuntimeError("x"), run_id=uuid4())
    assert list(t._store.store.iter_spans()) == []


def test_llm_end_with_no_start_is_noop(tmp_path: Path) -> None:
    """on_llm_end for an unknown run_id is silently ignored."""
    cb, t = _make_handler(tmp_path)
    cb.on_llm_end("ok", run_id=uuid4())
    assert list(t._store.store.iter_spans()) == []


def test_tool_error_writes_error_span(tmp_path: Path) -> None:
    """on_tool_error marks the tool span as ERROR."""
    cb, t = _make_handler(tmp_path)
    run_id = uuid4()
    cb.on_tool_start(
        {"name": "search"},
        "query",
        run_id=run_id,
    )
    cb.on_tool_error(RuntimeError("timeout"), run_id=run_id)
    spans = list(t._store.store.iter_spans())
    assert len(spans) == 1
    assert spans[0].name == "search"
    assert spans[0].status.value == "ERROR"
    assert "timeout" in (spans[0].error or "")


def test_tool_error_with_no_start_is_noop(tmp_path: Path) -> None:
    """on_tool_error for an unknown run_id is silently ignored."""
    cb, t = _make_handler(tmp_path)
    cb.on_tool_error(RuntimeError("x"), run_id=uuid4())
    assert list(t._store.store.iter_spans()) == []


def test_tool_end_with_no_start_is_noop(tmp_path: Path) -> None:
    """on_tool_end for an unknown run_id is silently ignored."""
    cb, t = _make_handler(tmp_path)
    cb.on_tool_end("result", run_id=uuid4())
    assert list(t._store.store.iter_spans()) == []


# ---------------------------------------------------------------------------
# Span type classification
# ---------------------------------------------------------------------------


def test_classify_openai_llm(tmp_path: Path) -> None:
    """on_llm_start with 'OpenAI' is LLM."""
    cb, t = _make_handler(tmp_path)
    rid = uuid4()
    cb.on_llm_start({"name": "OpenAI"}, ["p"], run_id=rid)
    cb.on_llm_end("ok", run_id=rid)
    span = next(t._store.store.iter_spans())
    assert span.type.value == "LLM"


def test_classify_empty_serialized_is_observation(tmp_path: Path) -> None:
    """No serialized name falls back to OBSERVATION."""
    cb, t = _make_handler(tmp_path)
    rid = uuid4()
    cb.on_chain_start({}, {"x": 1}, run_id=rid)
    cb.on_chain_end({"y": 2}, run_id=rid)
    span = next(t._store.store.iter_spans())
    assert span.type.value == "OBSERVATION"
