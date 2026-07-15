"""In-process scaling benchmark.

Run as ``clew bench`` from the CLI, or import :func:`bench` directly.
All operations run in a fresh tempdir; the existing store is not
touched.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from clew.core.branch import BranchManager
from clew.core.diff import diff as diff_traces
from clew.core.health import gc
from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore


def _make_trace(idx: int, n: int) -> tuple[str, list[Span]]:
    """Build a synthetic trace with ``n`` spans.

    The root span gets a unique ``name`` per trace so the diff
    engine has work to do (the diff between two distinct traces
    is non-empty).
    """
    tid = uuid.uuid4().hex
    t0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    spans: list[Span] = []
    root = Span(
        id=uuid.uuid4().hex,
        trace_id=tid,
        parent_ids=[],
        sequence=0,
        type=SpanType.OBSERVATION,
        name=f"root-{idx}",
        attributes={"i": idx},
        output=f"trace {idx}",
        started_at=t0,
        ended_at=t0 + timedelta(milliseconds=1),
        status=SpanStatus.OK,
    )
    spans.append(root)
    parent = root
    for j in range(1, n):
        s = Span(
            id=uuid.uuid4().hex,
            trace_id=tid,
            parent_ids=[parent.id],
            sequence=j,
            type=SpanType.LLM if j % 2 == 0 else SpanType.TOOL,
            name=f"step-{j}",
            attributes={"i": j, "trace_i": idx},
            output=f"out-{idx}-{j}",
            started_at=t0 + timedelta(milliseconds=j),
            ended_at=t0 + timedelta(milliseconds=j + 1),
            status=SpanStatus.OK,
        )
        spans.append(s)
        parent = s
    return tid, spans


def bench(root: Path, *, n_traces: int, spans_per_trace: int, n_orphans: int) -> dict[str, Any]:
    """Run the scaling benchmark.

    Writes to ``root`` (a fresh, empty clew store), records
    ``n_traces`` traces of ``spans_per_trace`` spans each, diffs
    the first and last traces, runs GC on ``n_orphans`` orphan
    spans, and reports the timings.

    The result is a JSON-serializable dict suitable for CI or
    manual ``--out`` output.
    """
    if isinstance(n_traces, bool) or not isinstance(n_traces, int) or n_traces < 1:
        raise ValueError("n_traces must be a positive integer")
    if (
        isinstance(spans_per_trace, bool)
        or not isinstance(spans_per_trace, int)
        or spans_per_trace < 1
    ):
        raise ValueError("spans_per_trace must be a positive integer")
    if isinstance(n_orphans, bool) or not isinstance(n_orphans, int) or n_orphans < 0:
        raise ValueError("n_orphans must be a non-negative integer")

    s = Store(root)
    ts = TraceStore(s)
    result: dict[str, Any] = {
        "traces_recorded": 0,
        "spans_per_trace": spans_per_trace,
        "n_orphans": n_orphans,
        "dedup_inputs": 0,
        "dedup_unique": 0,
    }
    first_tid = ""
    last_tid = ""
    t0 = time.perf_counter()
    for i in range(n_traces):
        tid, spans = _make_trace(i, spans_per_trace)
        for sp in spans:
            ts.add_span(sp)
        if i < max(1, n_traces // 2):
            BranchManager(ts).create("trace-" + str(i), spans[0].id)
        if i == 0:
            first_tid = tid
        last_tid = tid
    result["record_ms"] = (time.perf_counter() - t0) * 1000
    result["traces_recorded"] = n_traces

    # Diff first vs last
    a = ts.get_trace(first_tid)
    b = ts.get_trace(last_tid)
    t0 = time.perf_counter()
    d = diff_traces(a, b)
    result["diff_ms"] = (time.perf_counter() - t0) * 1000
    result["diff_added"] = len(d.added)
    result["diff_removed"] = len(d.removed)
    result["diff_changed"] = len(d.modified)

    # Occurrence identity sanity: identical payloads remain independent.
    now = datetime.now(UTC)
    occurrence_ids: set[str] = set()
    for _ in range(2):
        occurrence = Span(
            id=uuid.uuid4().hex,
            trace_id=uuid.uuid4().hex,
            parent_ids=[],
            sequence=0,
            type=SpanType.OBSERVATION,
            name="same-input",
            input={"value": 1},
            started_at=now,
            ended_at=now,
            status=SpanStatus.OK,
        )
        s.put(occurrence)
        occurrence_ids.add(occurrence.id)
    result["dedup_inputs"] = 2
    result["dedup_unique"] = len(occurrence_ids)

    # GC: write orphans, then GC
    now = datetime.now(UTC)
    for _ in range(n_orphans):
        s.put(
            Span(
                id=uuid.uuid4().hex,
                trace_id=uuid.uuid4().hex,
                parent_ids=[],
                sequence=0,
                type=SpanType.OBSERVATION,
                name="orphan",
                started_at=now,
                ended_at=now,
                status=SpanStatus.OK,
            )
        )
    t0 = time.perf_counter()
    r = gc(root, min_age_seconds=0)
    result["gc_ms"] = (time.perf_counter() - t0) * 1000
    result["orphans_scanned"] = r.scanned
    result["orphans_deleted"] = r.deleted
    return result
