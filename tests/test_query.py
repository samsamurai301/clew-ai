"""Tests for clew.core.query (clew query command)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from clew.core.branch import BranchManager
from clew.core.models import Span, SpanStatus, SpanType
from clew.core.query import QueryFilter, parse_metadata_spec, query
from clew.core.store import Store
from clew.core.trace import TraceStore


def _span(
    *,
    trace_id: str,
    parent_ids: list[str] | None = None,
    name: str = "root",
    type: SpanType = SpanType.OBSERVATION,
    status: SpanStatus = SpanStatus.OK,
    metadata: dict[str, object] | None = None,
    error: str | None = None,
) -> Span:
    return Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=parent_ids or [],
        type=type,
        name=name,
        attributes={},
        input="x",
        output="y",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 2, tzinfo=UTC),
        status=status,
        metadata=metadata,
        error=error,
    )


def _seed_two_traces(root: Path) -> tuple[str, str, list[Span]]:
    """Seed two traces with a few spans each, and return (trace_a, trace_b, all_spans)."""
    store = Store(root)
    ts = TraceStore(store)
    spans: list[Span] = []
    # Trace A: llm + tool + chain root
    tid_a = uuid4().hex
    a_root = _span(trace_id=tid_a, name="agent-run", type=SpanType.OBSERVATION)
    a_llm = _span(trace_id=tid_a, parent_ids=[a_root.id], name="gpt-4o-call", type=SpanType.LLM, metadata={"model": "gpt-4o"})
    a_tool = _span(
        trace_id=tid_a,
        parent_ids=[a_llm.id],
        name="search-tool",
        type=SpanType.TOOL,
        status=SpanStatus.ERROR,
        error="tool failed: timeout",
        metadata={"tool_name": "search"},
    )
    # Trace B: simpler
    tid_b = uuid4().hex
    b_root = _span(trace_id=tid_b, name="agent-run", type=SpanType.OBSERVATION, metadata={"model": "claude-3"})
    b_llm = _span(trace_id=tid_b, parent_ids=[b_root.id], name="claude-call", type=SpanType.LLM)
    for s in (a_root, a_llm, a_tool, b_root, b_llm):
        ts.add_span(s)
        spans.append(s)
    # Move main onto the first root so ref-checking passes.
    BranchManager(ts).move("main", a_root.id)
    return tid_a, tid_b, spans


# ---------------------------------------------------------------------------
# Basic filter behavior
# ---------------------------------------------------------------------------


def test_query_no_filters_returns_everything(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(limit=100))
    assert len(r) == 5


def test_query_filter_by_name_substring(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(name="gpt-4o"))
    assert len(r) == 1
    assert r[0].span.name == "gpt-4o-call"


def test_query_filter_by_name_is_case_insensitive(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(name="GPT-4O"))
    assert len(r) == 1


def test_query_filter_by_type(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(type=SpanType.LLM))
    assert {res.span.name for res in r} == {"gpt-4o-call", "claude-call"}


def test_query_filter_by_status(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(status=SpanStatus.ERROR))
    assert len(r) == 1
    assert r[0].span.name == "search-tool"


def test_query_filter_by_trace_id(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    tid_a, tid_b, _ = _seed_two_traces(root)
    r = query(root, QueryFilter(trace_id=tid_a))
    assert {res.span.name for res in r} == {"agent-run", "gpt-4o-call", "search-tool"}


def test_query_filter_by_metadata_string(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(metadata={"model": "gpt-4o"}))
    assert len(r) == 1
    assert r[0].span.name == "gpt-4o-call"


def test_query_filter_by_metadata_int(tmp_path: Path) -> None:
    """Numeric metadata values match even when stored as JSON int."""
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    # Add a span with a numeric metadata field.
    store = Store(root)
    ts = TraceStore(store)
    extra = _span(trace_id=uuid4().hex, name="scored", metadata={"score": 3})
    ts.add_span(extra)
    r = query(root, QueryFilter(metadata={"score": 3}))
    assert len(r) == 1
    assert r[0].span.name == "scored"


def test_query_filter_by_metadata_multiple_keys(tmp_path: Path) -> None:
    """All metadata keys must match (logical AND)."""
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(metadata={"model": "gpt-4o", "x": "nope"}))
    assert r == []


def test_query_combined_filters(tmp_path: Path) -> None:
    """name + type + status all apply."""
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(
        root,
        QueryFilter(name="search", type=SpanType.TOOL, status=SpanStatus.ERROR),
    )
    assert len(r) == 1
    assert r[0].span.name == "search-tool"


def test_query_limit_caps_results(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(limit=2))
    assert len(r) == 2


def test_query_empty_store(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    Store(root)
    r = query(root, QueryFilter())
    assert r == []


def test_query_no_match_returns_empty(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed_two_traces(root)
    r = query(root, QueryFilter(name="does-not-exist"))
    assert r == []


# ---------------------------------------------------------------------------
# parse_metadata_spec
# ---------------------------------------------------------------------------


def test_parse_metadata_spec_string() -> None:
    assert parse_metadata_spec(["model=gpt-4o"]) == {"model": "gpt-4o"}


def test_parse_metadata_spec_int() -> None:
    assert parse_metadata_spec(["n=3"]) == {"n": 3}


def test_parse_metadata_spec_bool() -> None:
    assert parse_metadata_spec(["ok=true"]) == {"ok": True}


def test_parse_metadata_spec_null() -> None:
    assert parse_metadata_spec(["x=null"]) == {"x": None}


def test_parse_metadata_spec_multiple() -> None:
    out = parse_metadata_spec(["a=1", "b=two"])
    assert out == {"a": 1, "b": "two"}


def test_parse_metadata_spec_empty_value_is_empty_string() -> None:
    assert parse_metadata_spec(["k="]) == {"k": ""}


def test_parse_metadata_spec_rejects_no_equals() -> None:
    with pytest.raises(ValueError, match="key=value"):
        parse_metadata_spec(["justakey"])


# ---------------------------------------------------------------------------
# QueryResult shape
# ---------------------------------------------------------------------------


def test_query_result_includes_trace_provenance(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    tid_a, _tid_b, _ = _seed_two_traces(root)
    r = query(root, QueryFilter(trace_id=tid_a, name="gpt-4o-call"))
    assert len(r) == 1
    res = r[0]
    assert res.trace_id == tid_a
    assert res.root_span_id  # non-empty
    assert res.span.name == "gpt-4o-call"
