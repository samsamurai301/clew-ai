"""Tests for the trace diff engine (structural comparison)."""

from __future__ import annotations

import time
from pathlib import Path

from clew.core.diff import diff, format_json, format_text
from clew.core.models import Trace
from clew.core.store import Store
from clew.core.trace import TraceStore

from .conftest import make_span  # type: ignore[import-not-found]


def _setup(tmp_path: Path) -> TraceStore:
    store = Store(tmp_path)
    return TraceStore(store)


def _build_three_step(ts: TraceStore) -> tuple[str, str, str, str, str]:
    """Return (root_id, child_id, leaf_id, trace_id_a, trace_id_b)."""
    root = make_span(name="root", trace_id="ta", output="r")
    child = make_span(name="child", trace_id="ta", parent_ids=[root.id], output="c")
    leaf = make_span(name="leaf", trace_id="ta", parent_ids=[child.id], output="l")
    ts.add_span(root)
    ts.add_span(child)
    ts.add_span(leaf)
    return root.id, child.id, leaf.id, root.trace_id, make_span(trace_id="tb").trace_id


def test_identical_traces_have_no_differences(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    root = make_span(name="root", trace_id="t", output="r")
    child = make_span(name="child", trace_id="t", parent_ids=[root.id], output="c")
    ts.add_span(root)
    ts.add_span(child)
    a = ts.get_trace(root.trace_id)
    # Use a second trace_id for the "compare" side. We need another
    # identical set of spans under a different trace id.
    root2 = make_span(name="root", trace_id="t2", output="r")
    child2 = make_span(name="child", trace_id="t2", parent_ids=[root2.id], output="c")
    ts.add_span(root2)
    ts.add_span(child2)
    b = ts.get_trace(root2.trace_id)
    d = diff(a, b)
    assert d.added == []
    assert d.removed == []
    assert d.modified == []
    assert d.unchanged_count == 2


def test_modified_span_detected(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    root = make_span(name="root", trace_id="t", output="r1")
    ts.add_span(root)
    a = ts.get_trace(root.trace_id)
    root2 = make_span(name="root", trace_id="t2", output="r2")
    ts.add_span(root2)
    b = ts.get_trace(root2.trace_id)
    d = diff(a, b)
    assert len(d.modified) == 1
    assert d.modified[0][0].output == "r1"
    assert d.modified[0][1].output == "r2"


def test_added_span_detected(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    a_root = make_span(name="root", trace_id="ta", output="r")
    ts.add_span(a_root)
    a = ts.get_trace(a_root.trace_id)
    b_root = make_span(name="root", trace_id="tb", output="r")
    b_child = make_span(name="child", trace_id="tb", parent_ids=[b_root.id], output="c")
    ts.add_span(b_root)
    ts.add_span(b_child)
    b = ts.get_trace(b_root.trace_id)
    d = diff(a, b)
    assert len(d.added) == 1
    assert d.added[0].name == "child"


def test_removed_span_detected(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    a_root = make_span(name="root", trace_id="ta", output="r")
    a_child = make_span(name="child", trace_id="ta", parent_ids=[a_root.id], output="c")
    ts.add_span(a_root)
    ts.add_span(a_child)
    a = ts.get_trace(a_root.trace_id)
    b_root = make_span(name="root", trace_id="tb", output="r")
    ts.add_span(b_root)
    b = ts.get_trace(b_root.trace_id)
    d = diff(a, b)
    assert len(d.removed) == 1
    assert d.removed[0].name == "child"


def test_format_text_non_empty(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    a_root = make_span(name="root", trace_id="ta", output="r")
    ts.add_span(a_root)
    a = ts.get_trace(a_root.trace_id)
    b_root = make_span(name="root", trace_id="tb", output="r2")
    ts.add_span(b_root)
    b = ts.get_trace(b_root.trace_id)
    d = diff(a, b)
    text = format_text(d)
    assert "root" in text
    assert a.trace_id in text
    assert b.trace_id in text


def test_format_json_roundtrips(tmp_path: Path) -> None:
    import json

    ts = _setup(tmp_path)
    a_root = make_span(name="root", trace_id="ta", output="r")
    ts.add_span(a_root)
    a = ts.get_trace(a_root.trace_id)
    b_root = make_span(name="root", trace_id="tb", output="r2")
    ts.add_span(b_root)
    b = ts.get_trace(b_root.trace_id)
    d = diff(a, b)
    raw = format_json(d)
    parsed = json.loads(raw)
    assert parsed["trace_a"] == a_root.trace_id
    assert parsed["trace_b"] == b_root.trace_id
    assert len(parsed["modified"]) == 1


def test_diff_is_deterministic(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    _, _, _, ta, tb = _build_three_step(ts)
    a = ts.get_trace(ta)
    # Add an extra span to b to force some asymmetry.
    extra = make_span(name="extra", trace_id=tb, output="e")
    ts.add_span(extra)
    b = ts.get_trace(tb)
    d1 = diff(a, b)
    d2 = diff(a, b)
    assert [s.name for s in d1.added] == [s.name for s in d2.added]
    assert [s.name for s in d1.removed] == [s.name for s in d2.removed]


def test_repeated_sibling_names_match_by_occurrence_order(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    root_a = make_span(name="root", trace_id="repeat-a", output="root")
    first_a = make_span(name="tool", trace_id="repeat-a", parent_ids=[root_a.id], output="same")
    second_a = make_span(name="tool", trace_id="repeat-a", parent_ids=[root_a.id], output="before")
    root_b = make_span(name="root", trace_id="repeat-b", output="root")
    first_b = make_span(name="tool", trace_id="repeat-b", parent_ids=[root_b.id], output="same")
    second_b = make_span(name="tool", trace_id="repeat-b", parent_ids=[root_b.id], output="after")
    for span in (root_a, first_a, second_a, root_b, first_b, second_b):
        ts.add_span(span)
    result = diff(ts.get_trace(root_a.trace_id), ts.get_trace(root_b.trace_id))
    assert result.added == []
    assert result.removed == []
    assert result.unchanged_count == 2
    assert len(result.modified) == 1
    assert result.modified[0][0].output == "before"
    assert result.modified[0][1].output == "after"


def test_text_diff_neutralizes_terminal_controls(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    hostile = "tool\x1b]8;;https://example.invalid\x07spoof"
    a = make_span(name=hostile, trace_id="term-a", output="before")
    b = make_span(name=hostile, trace_id="term-b", output="after")
    ts.add_span(a)
    ts.add_span(b)
    rendered = format_text(diff(ts.get_trace(a.trace_id), ts.get_trace(b.trace_id)))
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "\\u001b" in rendered
    assert "\\u0007" in rendered


def test_deep_chain_diff_is_not_quadratic() -> None:
    def chain(trace_id: str, suffix: str) -> Trace:
        spans = []
        parent_ids: list[str] = []
        for sequence in range(2_000):
            span = make_span(
                name=f"step-{sequence}",
                trace_id=trace_id,
                parent_ids=parent_ids,
                output=f"{suffix}-{sequence}",
                sequence=sequence,
            )
            spans.append(span)
            parent_ids = [span.id]
        return Trace(trace_id=spans[0].trace_id, root_span_id=spans[0].id, spans=spans)

    trace_a = chain("deep-a", "a")
    trace_b = chain("deep-b", "b")
    started = time.monotonic()
    result = diff(trace_a, trace_b)
    elapsed = time.monotonic() - started
    assert len(result.modified) == 2_000
    assert elapsed < 2.0
