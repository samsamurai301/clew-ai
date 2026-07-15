"""Tests for the HTML trace report (clew.core.html_report)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from clew.core.html_report import render_html, write_html
from clew.core.models import Span, SpanStatus, SpanType, Trace


def _span(
    trace_id: str,
    name: str = "root",
    parent_ids: list[str] | None = None,
    status: SpanStatus = SpanStatus.OK,
    type: SpanType = SpanType.OBSERVATION,
    error: str | None = None,
    input: object = "x",
    output: object = "y",
    sequence: int | None = None,
) -> Span:
    return Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=parent_ids or [],
        sequence=(1 if parent_ids else 0) if sequence is None else sequence,
        type=type,
        name=name,
        attributes={"k": "v"},
        input=input,
        output=output,
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
        status=status,
        error=error,
    )


def _trace(*spans: Span) -> Trace:
    return Trace(trace_id=spans[0].trace_id, root_span_id=spans[0].id, spans=list(spans))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_html_includes_doctype() -> None:
    tid = uuid4().hex
    s = _span(tid)
    out = render_html(_trace(s))
    assert out.startswith("<!DOCTYPE html>")


def test_render_html_includes_trace_id() -> None:
    tid = uuid4().hex
    s = _span(tid, name="solo")
    out = render_html(_trace(s))
    # The first 16 chars of the trace id are in the title.
    assert tid[:16] in out
    assert "solo" in out


def test_render_html_marks_error_spans() -> None:
    tid = uuid4().hex
    s = _span(tid, name="bad", status=SpanStatus.ERROR, error="kaboom")
    out = render_html(_trace(s))
    assert "span error" in out
    assert "kaboom" in out


def test_render_html_escapes_html_in_names() -> None:
    """Names with HTML characters don't break the page."""
    tid = uuid4().hex
    s = _span(tid, name="<script>alert(1)</script>")
    out = render_html(_trace(s))
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_render_html_includes_input_and_output() -> None:
    tid = uuid4().hex
    s = _span(tid, input={"q": "what is clew"}, output="answer")
    out = render_html(_trace(s))
    assert "what is clew" in out
    assert "answer" in out


def test_render_html_renders_children() -> None:
    """A trace with a child renders both root and child."""
    tid = uuid4().hex
    root = _span(tid, name="root")
    child = _span(tid, name="child", parent_ids=[root.id])
    out = render_html(_trace(root, child))
    assert "root" in out
    assert "child" in out


def test_render_html_handles_empty_trace() -> None:
    """render_html does not crash on a single-span trace (the minimum)."""
    tid = uuid4().hex
    s = _span(tid, name="lone")
    out = render_html(_trace(s))
    assert "lone" in out


def test_render_html_handles_more_than_python_recursion_limit() -> None:
    trace_id = uuid4().hex
    spans: list[Span] = []
    for sequence in range(1_100):
        spans.append(
            _span(
                trace_id,
                name=f"step-{sequence}",
                parent_ids=[spans[-1].id] if spans else [],
                sequence=sequence,
            )
        )
    output = render_html(_trace(*spans))
    assert "step-1099" in output
    assert output.count('<li><div class="span') == len(spans)


def test_render_html_renders_multi_parent_span_once() -> None:
    trace_id = uuid4().hex
    root = _span(trace_id, name="root", sequence=0)
    left = _span(trace_id, name="left", parent_ids=[root.id], sequence=1)
    right = _span(trace_id, name="right", parent_ids=[root.id], sequence=2)
    join = _span(
        trace_id,
        name="join",
        parent_ids=[left.id, right.id],
        sequence=3,
    )
    output = render_html(_trace(root, left, right, join))
    assert output.count('<li><div class="span') == 4


def test_render_html_includes_metadata() -> None:
    tid = uuid4().hex
    s = _span(tid)
    s2 = s.model_copy(update={"metadata": {"model": "gpt-4o", "tokens": 100}})
    out = render_html(_trace(s2))
    assert "gpt-4o" in out


def test_render_html_includes_collapse_javascript() -> None:
    """The HTML page is interactive (click handlers for collapse)."""
    tid = uuid4().hex
    s = _span(tid)
    out = render_html(_trace(s))
    assert "addEventListener" in out
    assert "details" in out


# ---------------------------------------------------------------------------
# write_html
# ---------------------------------------------------------------------------


def test_write_html_creates_file(tmp_path: Path) -> None:
    tid = uuid4().hex
    s = _span(tid)
    out = write_html(_trace(s), tmp_path / "report.html")
    assert out.exists()
    assert out.stat().st_size > 0
    text = out.read_text(encoding="utf-8")
    assert "DOCTYPE" in text


def test_write_html_self_contained(tmp_path: Path) -> None:
    """The HTML file has no external resource references (offline-safe)."""
    tid = uuid4().hex
    s = _span(tid)
    out = write_html(_trace(s), tmp_path / "report.html")
    text = out.read_text(encoding="utf-8")
    # No <link rel="stylesheet" href=...> with http
    assert "http://" not in text or "w3.org" in text
    assert "https://" not in text or "github.com/samsamurai301/clew-ai" in text
    # The github.com link in the footer is the only external reference, and
    # it's just an attribution.


# ---------------------------------------------------------------------------
# Security: HTML/JS injection defense
# ---------------------------------------------------------------------------


def test_html_report_handles_format_braces_in_span_content() -> None:
    """Attacker-controlled braces in span content cannot break the template."""
    from datetime import UTC, datetime

    trace_id = "b" * 32
    span = Span(
        id="a" * 32,
        trace_id=trace_id,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="{generated_at.__class__}",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )
    trace = Trace(
        trace_id=trace_id,
        root_span_id="a" * 32,
        spans=[span],
    )
    out = render_html(trace)
    assert "{generated_at.__class__}" in out


def test_html_report_escapes_html_in_name() -> None:
    """Span names with HTML are escaped, not interpreted."""
    from datetime import UTC, datetime

    span = Span(
        id="a" * 32,
        trace_id="b" * 32,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="<script>alert(1)</script>",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )
    trace = Trace(trace_id="b" * 32, root_span_id="a" * 32, spans=[span])
    out = render_html(trace)
    # The literal <script> must be escaped to &lt;script&gt;
    assert "<script>alert" not in out
    assert "&lt;script&gt;alert" in out
