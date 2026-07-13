"""Tests for clew.ui.render (rich renderables for traces, diffs, logs)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from clew.core.diff import diff as diff_traces
from clew.core.models import Span, SpanStatus, SpanType, Trace
from clew.ui.render import _span_label, render_diff, render_log, render_span_tree


def _span(
    trace_id: str,
    name: str,
    parent_ids: list[str] | None = None,
    status: SpanStatus = SpanStatus.OK,
    type: SpanType = SpanType.OBSERVATION,
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
        error=error,
    )


def test_render_span_tree_returns_renderable() -> None:
    tid = uuid4().hex
    root = _span(tid, "root")
    child = _span(tid, "child", parent_ids=[root.id])
    trace = Trace(trace_id=tid, root_span_id=root.id, spans=[root, child])
    r = render_span_tree(trace)
    # The exact type is a Tree; the important thing is it renders.
    assert r is not None


def test_render_diff_returns_renderable() -> None:
    """render_diff produces a renderable for a TraceDiff object."""
    tid_a, tid_b = uuid4().hex, uuid4().hex
    sa = _span(tid_a, "root")
    sb = _span(tid_b, "root")
    sb_mod = sb.model_copy(update={"output": "different"})
    trace_a = Trace(trace_id=tid_a, root_span_id=sa.id, spans=[sa])
    trace_b = Trace(trace_id=tid_b, root_span_id=sb_mod.id, spans=[sb_mod])
    d = diff_traces(trace_a, trace_b)
    r = render_diff(d)
    assert r is not None


def test_render_log_returns_table() -> None:
    rows = [
        {"trace_id": "abc", "root": "first", "spans": 1, "started": "2024-01-01T00:00:00Z"},
        {"trace_id": "def", "root": "second", "spans": 2, "started": "2024-01-02T00:00:00Z"},
    ]
    table = render_log(rows)
    assert table.row_count == 2


def test_render_log_empty() -> None:
    table = render_log([])
    assert table.row_count == 0


def test_span_label_includes_name_and_status() -> None:
    s = _span(uuid4().hex, "my-span", status=SpanStatus.ERROR, error="boom")
    label = _span_label(s)
    text = label.plain
    assert "my-span" in text
    assert "ERROR" in text


def test_span_label_no_error_text_when_ok() -> None:
    s = _span(uuid4().hex, "ok-span", status=SpanStatus.OK)
    label = _span_label(s)
    text = label.plain
    assert "ok-span" in text
    assert "ERROR" not in text
