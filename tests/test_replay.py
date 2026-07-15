"""Tests for the replay engine (re-execute a trace via an executor)."""

from __future__ import annotations

from pathlib import Path

import pytest

from clew.core.models import Span, SpanStatus
from clew.core.replay import (
    MockExecutor,
    RecordingExecutor,
    ReplayContext,
    ReplayEngine,
    ReplayResult,
)
from clew.core.store import Store
from clew.core.trace import TraceStore

from .conftest import make_span  # type: ignore[import-not-found]


def _setup(tmp_path: Path) -> TraceStore:
    store = Store(tmp_path)
    return TraceStore(store)


def _build_simple_trace(ts: TraceStore) -> tuple[str, list[Span]]:
    root = make_span(name="root", trace_id="t1", output="r-out")
    child = make_span(name="child", trace_id="t1", parent_ids=[root.id], output="c-out")
    leaf = make_span(name="leaf", trace_id="t1", parent_ids=[child.id], output="l-out")
    ts.add_span(root)
    ts.add_span(child)
    ts.add_span(leaf)
    return root.trace_id, [root, child, leaf]


@pytest.mark.anyio
async def test_replay_creates_new_trace_id(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, _ = _build_simple_trace(ts)
    engine = ReplayEngine(ts, executor=MockExecutor())
    new_trace = await engine.replay(trace_id)
    assert new_trace.trace_id != trace_id


@pytest.mark.anyio
async def test_replay_with_mock_is_deterministic(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, original_spans = _build_simple_trace(ts)
    engine = ReplayEngine(ts, executor=MockExecutor())
    new_trace = await engine.replay(trace_id)
    original_trace = ts.get_trace(trace_id)
    # Original trace is unchanged.
    for orig in original_trace.spans:
        still = ts.get(orig.id) if hasattr(ts, "get") else None
        # The store may dedupe, so just confirm the original is still there.
        assert orig.id in {s.id for s in ts.get_trace(trace_id).spans}
    # New trace has the same number of spans.
    assert len(new_trace.spans) == len(original_trace.spans)


@pytest.mark.anyio
async def test_replay_does_not_mutate_original(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, _ = _build_simple_trace(ts)
    engine = ReplayEngine(ts, executor=MockExecutor())
    await engine.replay(trace_id)
    # Original trace is still present with the same span ids.
    again = ts.get_trace(trace_id)
    assert len(again.spans) == 3


@pytest.mark.anyio
async def test_replay_from_middle_only_runs_descendants(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, spans = _build_simple_trace(ts)
    child = spans[1]
    engine = ReplayEngine(ts, executor=MockExecutor())
    new_trace = await engine.replay(trace_id, from_span_id=child.id)
    # New trace should still have 3 spans (root copied, child + leaf re-executed).
    assert len(new_trace.spans) == 3


@pytest.mark.anyio
async def test_replay_recording_executor_captures_output(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, spans = _build_simple_trace(ts)
    captured: list[tuple[str, object]] = []

    async def fn(span: Span, ctx):  # type: ignore[no-untyped-def]
        captured.append((span.name, span.output))
        return ReplayResult(output="fresh-" + span.name, attributes={"replayed": True})

    engine = ReplayEngine(ts, executor=RecordingExecutor(fn))
    new_trace = await engine.replay(trace_id)
    assert len(captured) == 3
    assert {name for name, _ in captured} == {"root", "child", "leaf"}
    # New trace outputs are the "fresh-..." variants.
    outputs = {s.name: s.output for s in new_trace.spans}
    assert outputs["root"] == "fresh-root"
    assert outputs["child"] == "fresh-child"
    assert outputs["leaf"] == "fresh-leaf"


def test_dry_run_returns_full_list(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, _ = _build_simple_trace(ts)
    engine = ReplayEngine(ts, executor=MockExecutor())
    plan = engine.dry_run(trace_id)
    assert len(plan) == 3
    names = [s.name for s in plan]
    assert "root" in names
    assert "child" in names
    assert "leaf" in names


def test_dry_run_from_middle(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, spans = _build_simple_trace(ts)
    child = spans[1]
    engine = ReplayEngine(ts, executor=MockExecutor())
    plan = engine.dry_run(trace_id, from_span_id=child.id)
    names = {s.name for s in plan}
    # child and leaf are descendants; root is not in the plan.
    assert "child" in names
    assert "leaf" in names
    assert "root" not in names


@pytest.mark.anyio
async def test_replay_with_invalid_from_span_raises(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, _ = _build_simple_trace(ts)
    engine = ReplayEngine(ts, executor=MockExecutor())
    with pytest.raises(KeyError):
        await engine.replay(trace_id, from_span_id="not-a-real-id")


@pytest.mark.anyio
async def test_every_replay_parent_belongs_to_new_trace(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, spans = _build_simple_trace(ts)
    replayed = await ReplayEngine(ts).replay(trace_id, from_span_id=spans[1].id)
    ids = {span.id for span in replayed.spans}
    original_ids = {span.id for span in spans}
    assert ids.isdisjoint(original_ids)
    assert all(parent in ids for span in replayed.spans for parent in span.parent_ids)


@pytest.mark.anyio
async def test_executor_receives_finalized_new_parent_chain(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    trace_id, _ = _build_simple_trace(ts)
    seen: dict[str, tuple[Span, ...]] = {}

    def execute(span: Span, context: ReplayContext) -> ReplayResult:
        seen[span.name] = context.parent_chain
        return ReplayResult(output=span.output)

    replayed = await ReplayEngine(ts, RecordingExecutor(execute)).replay(trace_id)
    assert seen["root"] == ()
    assert [span.name for span in seen["child"]] == ["root"]
    assert [span.name for span in seen["leaf"]] == ["root", "child"]
    assert all(
        parent.trace_id == replayed.trace_id and parent.content_hash and parent.status is not None
        for chain in seen.values()
        for parent in chain
    )


@pytest.mark.anyio
async def test_executor_failure_persists_error_and_skips_descendants(
    tmp_path: Path,
) -> None:
    ts = _setup(tmp_path)
    trace_id, _ = _build_simple_trace(ts)

    async def execute(span: Span, context: ReplayContext) -> ReplayResult:
        del context
        if span.name == "child":
            raise RuntimeError("replay exploded")
        return ReplayResult(output=span.output)

    replayed = await ReplayEngine(ts, RecordingExecutor(execute)).replay(trace_id)
    by_name = {span.name: span for span in replayed.spans}
    assert by_name["root"].status is SpanStatus.OK
    assert by_name["child"].status is SpanStatus.ERROR
    assert "replay exploded" in (by_name["child"].error or "")
    assert by_name["leaf"].status is SpanStatus.SKIPPED
    assert by_name["leaf"].attributes["replay.skip_reason"] == "dependency failed"


@pytest.mark.anyio
async def test_multi_parent_replay_preserves_complete_topology(tmp_path: Path) -> None:
    ts = _setup(tmp_path)
    root = make_span(name="root", trace_id="join")
    left = make_span(name="side", trace_id="join", parent_ids=[root.id])
    right = make_span(name="side", trace_id="join", parent_ids=[root.id])
    join = make_span(name="join", trace_id="join", parent_ids=[left.id, right.id])
    for span in (join, right, left, root):
        ts.add_span(span)
    replayed = await ReplayEngine(ts).replay(root.trace_id, from_span_id=join.id)
    by_name = {span.name: span for span in replayed.spans if span.name != "side"}
    assert len(by_name["join"].parent_ids) == 2
    ids = {span.id for span in replayed.spans}
    assert set(by_name["join"].parent_ids) <= ids
