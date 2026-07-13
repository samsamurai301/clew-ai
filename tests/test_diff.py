"""Tests for the trace diff engine (structural comparison)."""

from __future__ import annotations

from pathlib import Path

from clew.core.diff import diff, format_json, format_text
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
    return root.id, child.id, leaf.id, "ta", "tb"


def test_identical_traces_have_no_differences(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    root = make_span(name="root", trace_id="t", output="r")
    child = make_span(name="child", trace_id="t", parent_ids=[root.id], output="c")
    ts.add_span(root)
    ts.add_span(child)
    a = ts.get_trace("t")
    # Use a second trace_id for the "compare" side. We need another
    # identical set of spans under a different trace id.
    root2 = make_span(name="root", trace_id="t2", output="r")
    child2 = make_span(name="child", trace_id="t2", parent_ids=[root2.id], output="c")
    ts.add_span(root2)
    ts.add_span(child2)
    b = ts.get_trace("t2")
    d = diff(a, b)
    assert d.added == []
    assert d.removed == []
    assert d.modified == []
    assert d.unchanged_count == 2


def test_modified_span_detected(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    root = make_span(name="root", trace_id="t", output="r1")
    ts.add_span(root)
    a = ts.get_trace("t")
    root2 = make_span(name="root", trace_id="t2", output="r2")
    ts.add_span(root2)
    b = ts.get_trace("t2")
    d = diff(a, b)
    assert len(d.modified) == 1
    assert d.modified[0][0].output == "r1"
    assert d.modified[0][1].output == "r2"


def test_added_span_detected(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    a_root = make_span(name="root", trace_id="ta", output="r")
    ts.add_span(a_root)
    a = ts.get_trace("ta")
    b_root = make_span(name="root", trace_id="tb", output="r")
    b_child = make_span(name="child", trace_id="tb", parent_ids=[b_root.id], output="c")
    ts.add_span(b_root)
    ts.add_span(b_child)
    b = ts.get_trace("tb")
    d = diff(a, b)
    assert len(d.added) == 1
    assert d.added[0].name == "child"


def test_removed_span_detected(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    a_root = make_span(name="root", trace_id="ta", output="r")
    a_child = make_span(name="child", trace_id="ta", parent_ids=[a_root.id], output="c")
    ts.add_span(a_root)
    ts.add_span(a_child)
    a = ts.get_trace("ta")
    b_root = make_span(name="root", trace_id="tb", output="r")
    ts.add_span(b_root)
    b = ts.get_trace("tb")
    d = diff(a, b)
    assert len(d.removed) == 1
    assert d.removed[0].name == "child"


def test_format_text_non_empty(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    a_root = make_span(name="root", trace_id="ta", output="r")
    ts.add_span(a_root)
    a = ts.get_trace("ta")
    b_root = make_span(name="root", trace_id="tb", output="r2")
    ts.add_span(b_root)
    b = ts.get_trace("tb")
    d = diff(a, b)
    text = format_text(d)
    assert "root" in text
    assert "ta" in text
    assert "tb" in text


def test_format_json_roundtrips(tmp_path: Path) -> None:
    import json
    ts = _setup(tmp_path)
    a_root = make_span(name="root", trace_id="ta", output="r")
    ts.add_span(a_root)
    a = ts.get_trace("ta")
    b_root = make_span(name="root", trace_id="tb", output="r2")
    ts.add_span(b_root)
    b = ts.get_trace("tb")
    d = diff(a, b)
    raw = format_json(d)
    parsed = json.loads(raw)
    assert parsed["trace_a"] == "ta"
    assert parsed["trace_b"] == "tb"
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
