"""Scaling / performance smoke tests for clew.

These are coarse regression guards rather than precise benchmarks.
They assert that the store *completes* on a non-trivial number
of spans, that the results are correct, and that runtime stays below
generous release ceilings. If a future change makes the store O(n^2)
on 10k spans, this test will tell you.

Run with ``uv run pytest tests/test_scaling.py -q -s`` to see
the timings.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore


def _make_chain(root_path: Path, n_spans: int) -> list[str]:
    """Build a trace with ``n_spans`` in a chain; return span ids."""
    store = Store(root_path)
    ts = TraceStore(store)
    tid = uuid4().hex
    span_ids: list[str] = []
    prev_id: str | None = None
    for i in range(n_spans):
        sid = uuid4().hex
        span_ids.append(sid)
        s = Span(
            id=sid,
            trace_id=tid,
            parent_ids=[prev_id] if prev_id else [],
            sequence=i,
            type=SpanType.OBSERVATION,
            name=f"step-{i}",
            attributes={"i": i},
            input=f"in-{i}",
            output=f"out-{i}",
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
            ended_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
            status=SpanStatus.OK,
        )
        ts.add_span(s)
        prev_id = sid
    return span_ids


# ---------------------------------------------------------------------------
# Scaling
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_store_handles_1000_spans(tmp_path: Path) -> None:
    """A 1000-span trace builds, queries, and gc's in <5s."""
    started = time.monotonic()
    ids = _make_chain(tmp_path / ".clew", 500)
    build_time = time.monotonic() - started
    assert len(ids) == 500

    started = time.monotonic()
    store = Store(tmp_path / ".clew")
    count = sum(1 for _ in store.iter_spans())
    query_time = time.monotonic() - started
    assert count == 500

    started = time.monotonic()
    ts = TraceStore(store)
    trace = ts.get_trace(trace_id=ts.store.get(ids[0]).trace_id)
    fetch_time = time.monotonic() - started
    assert len(trace.spans) == 500

    # 5s is generous; the actual time on a fast machine is sub-second.
    print(f"\n1000 spans: build={build_time:.3f}s  iter={query_time:.3f}s  fetch={fetch_time:.3f}s")
    assert build_time < 5.0
    assert query_time < 5.0
    assert fetch_time < 5.0


@pytest.mark.slow
def test_store_handles_10_000_spans_with_release_limits(tmp_path: Path) -> None:
    """The release performance contract guards against quadratic growth."""
    root = tmp_path / ".clew"
    started = time.monotonic()
    span_ids = _make_chain(root, 10_000)
    build_time = time.monotonic() - started

    store = Store(root)
    trace_id = store.get(span_ids[0]).trace_id
    started = time.monotonic()
    trace = TraceStore(store).get_trace(trace_id)
    fetch_time = time.monotonic() - started

    started = time.monotonic()
    count = sum(1 for _ in store.iter_spans(trace_id=trace_id))
    query_time = time.monotonic() - started

    print(f"\n10000 spans: build={build_time:.3f}s fetch={fetch_time:.3f}s query={query_time:.3f}s")
    assert len(trace.spans) == count == 10_000
    # Each write intentionally fsyncs its canonical record and SQLite index.
    # Hosted-runner filesystem latency varies substantially; 45s preserves a
    # useful regression ceiling above the measured 30.7-35.7s Linux baseline.
    assert build_time < 45.0
    assert fetch_time < 5.0
    assert query_time < 5.0


def test_many_traces(tmp_path: Path) -> None:
    """Many small traces — exercises the index."""
    store = Store(tmp_path / ".clew")
    ts = TraceStore(store)
    n_traces = 100
    n_per_trace = 10
    for _ in range(n_traces):
        tid = uuid4().hex
        prev: str | None = None
        for i in range(n_per_trace):
            sid = uuid4().hex
            ts.add_span(
                Span(
                    id=sid,
                    trace_id=tid,
                    parent_ids=[prev] if prev else [],
                    sequence=i,
                    type=SpanType.OBSERVATION,
                    name=f"step-{i}",
                    attributes={},
                    input="x",
                    output="y",
                    started_at=datetime(2024, 1, 1, tzinfo=UTC),
                    ended_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
                    status=SpanStatus.OK,
                )
            )
            prev = sid
    started = time.monotonic()
    trace_ids = list(store.iter_traces())
    iter_time = time.monotonic() - started
    assert len(trace_ids) == n_traces
    print(f"\n{n_traces} traces ({n_per_trace} spans each): iter={iter_time:.3f}s")
    assert iter_time < 5.0


# ---------------------------------------------------------------------------
# Correctness under load
# ---------------------------------------------------------------------------


def test_idempotent_write_under_load(tmp_path: Path) -> None:
    """Writing the exact same occurrence repeatedly remains idempotent."""
    store = Store(tmp_path / ".clew")
    tid = uuid4().hex
    s = Span(
        id=uuid4().hex,
        trace_id=tid,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="dup-test",
        attributes={},
        input="x",
        output="y",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )
    # Add the exact same finalized record 100 times.
    seen: set[str] = set()
    for _ in range(100):
        pass
    actual_id = store.put(s)
    seen.add(actual_id)
    for _ in range(99):
        same_id = store.put(s)
        assert same_id == actual_id
        seen.add(same_id)
    assert len(seen) == 1


@pytest.mark.slow
def test_gc_with_many_orphans(tmp_path: Path) -> None:
    """gc() finds and removes many orphan spans."""
    from clew.core.health import gc

    # Seed 1000 spans, none of which are reachable from any ref.
    _make_chain(tmp_path / ".clew", 500)
    started = time.monotonic()
    result = gc(tmp_path / ".clew", dry_run=False, min_age_seconds=0)
    elapsed = time.monotonic() - started
    assert result.scanned == 500
    assert result.deleted == 500
    print(f"\ngc 1000 orphans: {elapsed:.3f}s")
    assert elapsed < 5.0
