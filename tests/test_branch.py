"""Tests for the branch manager (git-style refs and HEAD)."""

from __future__ import annotations

from pathlib import Path

import pytest

from clew.core.branch import DEFAULT_BRANCH, BranchManager
from clew.core.store import Store
from clew.core.trace import TraceStore

from .conftest import make_span  # type: ignore[import-not-found]


def _setup(tmp_path: Path) -> tuple[BranchManager, TraceStore]:
    store = Store(tmp_path)
    ts = TraceStore(store)
    return BranchManager(ts), ts


def test_default_branch_created_on_init(tmp_path: Path) -> None:
    bm, _ = _setup(tmp_path)
    assert DEFAULT_BRANCH in {b.name for b in bm.list()}
    assert bm.current() == DEFAULT_BRANCH


def test_create_and_get(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    span = make_span(name="root", trace_id="t1")
    ts.add_span(span)
    bm.create("feature", span.id)
    got = bm.get("feature")
    assert got.name == "feature"
    assert got.head_span_id == span.id


def test_create_duplicate_raises(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    span = make_span(name="root", trace_id="t1")
    ts.add_span(span)
    bm.create("feature", span.id)
    with pytest.raises(FileExistsError):
        bm.create("feature", span.id)


def test_list_sorted_by_name(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    span = make_span(name="root", trace_id="t1")
    ts.add_span(span)
    bm.create("zeta", span.id)
    bm.create("alpha", span.id)
    bm.create("mu", span.id)
    names = [b.name for b in bm.list()]
    assert names == ["alpha", "main", "mu", "zeta"]


def test_delete(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    span = make_span(name="root", trace_id="t1")
    ts.add_span(span)
    bm.create("feature", span.id)
    bm.delete("feature")
    with pytest.raises(KeyError):
        bm.get("feature")


def test_delete_current_branch_raises(tmp_path: Path) -> None:
    bm, _ = _setup(tmp_path)
    with pytest.raises(ValueError):
        bm.delete(bm.current())


def test_checkout_switches_head(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    span = make_span(name="root", trace_id="t1")
    ts.add_span(span)
    bm.create("feature", span.id)
    bm.checkout("feature")
    assert bm.current() == "feature"
    assert bm.head_span_id() == span.id


def test_move_updates_branch(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    s1 = make_span(name="s1", trace_id="t1")
    s2 = make_span(name="s2", trace_id="t1", parent_ids=[s1.id])
    ts.add_span(s1)
    ts.add_span(s2)
    bm.create("feature", s1.id)
    bm.move("feature", s2.id)
    assert bm.get("feature").head_span_id == s2.id


def test_invalid_branch_name_rejected(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    span = make_span(name="root", trace_id="t1")
    ts.add_span(span)
    for bad in ("../escape", "with/slash", "with\\backslash", "", ".", "..", "null\0byte"):
        with pytest.raises(ValueError):
            bm.create(bad, span.id)


def test_two_branches_independent(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    root = make_span(name="root", trace_id="t1")
    child_a = make_span(name="child_a", trace_id="t1", parent_ids=[root.id])
    child_b = make_span(name="child_b", trace_id="t1", parent_ids=[root.id])
    ts.add_span(root)
    ts.add_span(child_a)
    ts.add_span(child_b)
    bm.create("branch-a", child_a.id)
    bm.create("branch-b", child_b.id)
    assert bm.get("branch-a").head_span_id == child_a.id
    assert bm.get("branch-b").head_span_id == child_b.id


def test_refs_persist_to_disk(tmp_path: Path) -> None:
    bm, ts = _setup(tmp_path)
    span = make_span(name="root", trace_id="t1")
    ts.add_span(span)
    bm.create("feature-x", span.id)
    ref_file = tmp_path / "refs" / "feature-x"
    assert ref_file.exists()
    content = ref_file.read_text(encoding="utf-8").strip()
    assert content == span.id


def test_get_missing_raises(tmp_path: Path) -> None:
    bm, _ = _setup(tmp_path)
    with pytest.raises(KeyError):
        bm.get("nope")


def test_checkout_missing_raises(tmp_path: Path) -> None:
    bm, _ = _setup(tmp_path)
    with pytest.raises(KeyError):
        bm.checkout("nope")
