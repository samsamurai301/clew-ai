"""Tests for clew.core.trace — TraceStore, walk, ancestors, descendants, roots."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from clew.core.errors import TraceTopologyError
from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_span(
    *,
    span_id: str,
    trace_id: str,
    parent_ids: list[str] | None = None,
    name: str = "test",
    span_type: SpanType = SpanType.LLM,
    sequence: int | None = None,
) -> Span:
    return Span(
        id=span_id,
        trace_id=trace_id,
        parent_ids=list(parent_ids or []),
        sequence=int(span_id[0], 16) if sequence is None else sequence,
        type=span_type,
        name=name,
        attributes={},
        started_at=datetime(2026, 7, 13, 18, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 7, 13, 18, 0, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    root = tmp_path / ".clew"
    return Store(root)


@pytest.fixture
def traces(store: Store) -> TraceStore:
    return TraceStore(store)


# ---------------------------------------------------------------------------
# add_span
# ---------------------------------------------------------------------------


def test_add_span_writes_to_store(traces: TraceStore) -> None:
    """add_span is a thin pass-through to store.put."""
    span = _make_span(span_id="a" * 32, trace_id="f" * 32, name="hello")
    assert traces.add_span(span) == span.id
    assert traces.store.has(span.id)


# ---------------------------------------------------------------------------
# walk
# ---------------------------------------------------------------------------


def test_walk_visits_parents_before_children(traces: TraceStore) -> None:
    """In a DFS pre-order walk, every parent is yielded before its children."""
    trace_id = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    child = _make_span(span_id="2" * 32, trace_id=trace_id, name="child", parent_ids=[root.id])
    grandchild = _make_span(
        span_id="3" * 32, trace_id=trace_id, name="grandchild", parent_ids=[child.id]
    )
    for s in (root, child, grandchild):
        traces.add_span(s)
    visited = [s.name for s in traces.walk(root.id)]
    assert visited.index("root") < visited.index("child")
    assert visited.index("child") < visited.index("grandchild")


def test_walk_handles_diamond(traces: TraceStore) -> None:
    """A diamond DAG is walked with each span yielded exactly once."""
    trace_id = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    left = _make_span(span_id="2" * 32, trace_id=trace_id, name="left", parent_ids=[root.id])
    right = _make_span(span_id="3" * 32, trace_id=trace_id, name="right", parent_ids=[root.id])
    join = _make_span(
        span_id="4" * 32,
        trace_id=trace_id,
        name="join",
        parent_ids=[left.id, right.id],
    )
    for s in (root, left, right, join):
        traces.add_span(s)
    visited = [s.name for s in traces.walk(root.id)]
    assert sorted(visited) == ["join", "left", "right", "root"]
    # And no duplicates.
    assert len(visited) == len(set(visited))
    # Root is first.
    assert visited[0] == "root"


def test_walk_handles_multiple_roots(traces: TraceStore) -> None:
    """walk from a particular root only visits its subgraph."""
    trace_id = "f" * 32
    r1 = _make_span(span_id="1" * 32, trace_id=trace_id, name="r1")
    r1_child = _make_span(span_id="2" * 32, trace_id=trace_id, name="r1_child", parent_ids=[r1.id])
    r2 = _make_span(span_id="3" * 32, trace_id=trace_id, name="r2")
    for s in (r1, r1_child, r2):
        traces.add_span(s)
    visited = [s.name for s in traces.walk(r1.id)]
    assert "r1" in visited
    assert "r1_child" in visited
    assert "r2" not in visited


# ---------------------------------------------------------------------------
# ancestors
# ---------------------------------------------------------------------------


def test_ancestors_root_first(traces: TraceStore) -> None:
    """ancestors returns the chain root-first, ending with the input span."""
    trace_id = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    middle = _make_span(span_id="2" * 32, trace_id=trace_id, name="middle", parent_ids=[root.id])
    leaf = _make_span(span_id="3" * 32, trace_id=trace_id, name="leaf", parent_ids=[middle.id])
    for s in (root, middle, leaf):
        traces.add_span(s)
    chain = [s.name for s in traces.ancestors(leaf.id)]
    assert chain == ["root", "middle", "leaf"]


def test_ancestors_for_root(traces: TraceStore) -> None:
    """ancestors of a root span is just that span."""
    trace_id = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    traces.add_span(root)
    chain = [s.name for s in traces.ancestors(root.id)]
    assert chain == ["root"]


# ---------------------------------------------------------------------------
# descendants
# ---------------------------------------------------------------------------


def test_descendants_includes_all_depths(traces: TraceStore) -> None:
    """descendants returns every descendant at any depth."""
    trace_id = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    a = _make_span(span_id="2" * 32, trace_id=trace_id, name="a", parent_ids=[root.id])
    b = _make_span(span_id="3" * 32, trace_id=trace_id, name="b", parent_ids=[root.id])
    c = _make_span(span_id="4" * 32, trace_id=trace_id, name="c", parent_ids=[a.id])
    for s in (root, a, b, c):
        traces.add_span(s)
    desc_names = sorted(s.name for s in traces.descendants(root.id))
    assert desc_names == ["a", "b", "c"]


def test_descendants_for_leaf(traces: TraceStore) -> None:
    """A leaf has no descendants."""
    trace_id = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    leaf = _make_span(span_id="2" * 32, trace_id=trace_id, name="leaf", parent_ids=[root.id])
    for s in (root, leaf):
        traces.add_span(s)
    assert traces.descendants(leaf.id) == []


def test_descendants_excludes_input(traces: TraceStore) -> None:
    """descendants does NOT include the input span itself."""
    trace_id = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    child = _make_span(span_id="2" * 32, trace_id=trace_id, name="child", parent_ids=[root.id])
    for s in (root, child):
        traces.add_span(s)
    desc = traces.descendants(root.id)
    assert all(s.id != root.id for s in desc)


@pytest.mark.parametrize("operation", ["walk", "ancestors", "descendants"])
def test_navigation_never_crosses_trace_boundaries(traces: TraceStore, operation: str) -> None:
    first_trace = "e" * 32
    second_trace = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=first_trace, name="root")
    foreign = _make_span(
        span_id="2" * 32,
        trace_id=second_trace,
        name="foreign",
        parent_ids=[root.id],
    )
    for span in (root, foreign):
        traces.add_span(span)

    if operation == "walk":
        assert [span.id for span in traces.walk(root.id)] == [root.id]
    elif operation == "descendants":
        assert traces.descendants(root.id) == []
    else:
        with pytest.raises(TraceTopologyError, match="different trace"):
            traces.ancestors(foreign.id)


# ---------------------------------------------------------------------------
# roots
# ---------------------------------------------------------------------------


def test_roots_returns_only_parentless_spans(traces: TraceStore) -> None:
    """roots() returns spans with empty parent_ids."""
    trace_id = "f" * 32
    r1 = _make_span(span_id="1" * 32, trace_id=trace_id, name="r1")
    r2 = _make_span(span_id="2" * 32, trace_id=trace_id, name="r2")
    child = _make_span(span_id="3" * 32, trace_id=trace_id, name="child", parent_ids=[r1.id])
    for s in (r1, r2, child):
        traces.add_span(s)
    root_names = sorted(s.name for s in traces.roots())
    assert root_names == ["r1", "r2"]


def test_roots_empty_store(traces: TraceStore) -> None:
    """An empty store has no roots."""
    assert traces.roots() == []


# ---------------------------------------------------------------------------
# get_trace
# ---------------------------------------------------------------------------


def test_get_trace_topological_order(traces: TraceStore) -> None:
    """get_trace returns spans in topological order (parents before children)."""
    trace_id = "f" * 32
    # Insert in reverse order to make sure topo sort works.
    leaf = _make_span(
        span_id="3" * 32,
        trace_id=trace_id,
        name="leaf",
        parent_ids=["2" * 32],
    )
    middle = _make_span(span_id="2" * 32, trace_id=trace_id, name="middle", parent_ids=["1" * 32])
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    for s in (leaf, middle, root):
        traces.add_span(s)
    trace = traces.get_trace(trace_id)
    names = [s.name for s in trace.spans]
    assert names.index("root") < names.index("middle") < names.index("leaf")


def test_get_trace_root_span_id(traces: TraceStore) -> None:
    """get_trace's root_span_id points to the actual root span."""
    trace_id = "f" * 32
    root = _make_span(span_id="1" * 32, trace_id=trace_id, name="root")
    child = _make_span(span_id="2" * 32, trace_id=trace_id, name="child", parent_ids=[root.id])
    for s in (root, child):
        traces.add_span(s)
    trace = traces.get_trace(trace_id)
    assert trace.root_span_id == root.id


def test_get_trace_raises_for_missing(traces: TraceStore) -> None:
    """get_trace raises KeyError for an unknown trace_id."""
    with pytest.raises(KeyError):
        traces.get_trace("f" * 32)


def test_get_trace_all_spans(traces: TraceStore) -> None:
    """get_trace contains every span for the trace."""
    trace_id = "f" * 32
    spans = [
        _make_span(span_id="1" * 32, trace_id=trace_id, name="r"),
        _make_span(span_id="2" * 32, trace_id=trace_id, name="a", parent_ids=["1" * 32]),
        _make_span(span_id="3" * 32, trace_id=trace_id, name="b", parent_ids=["1" * 32]),
    ]
    for s in spans:
        traces.add_span(s)
    trace = traces.get_trace(trace_id)
    assert {s.id for s in trace.spans} == {s.id for s in spans}
    assert trace.trace_id == trace_id
