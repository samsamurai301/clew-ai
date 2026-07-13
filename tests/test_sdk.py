"""Tests for the user-facing :class:`Tracer`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import anyio
import pytest

from clew.core.models import SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.sdk import Tracer


def test_tracer_creates_store_lazily(tmp_path: Path) -> None:
    """A Tracer without an explicit store creates ``.clew`` under cwd."""
    cwd = tmp_path / "project"
    cwd.mkdir()
    import os
    old = os.getcwd()
    os.chdir(cwd)
    try:
        t = Tracer()
        assert t.store.store.root.exists()
    finally:
        os.chdir(old)


def test_span_decorator_captures_input_and_output(tmp_path: Path) -> None:
    """A decorated function records its input and output as a span."""
    t = Tracer(cwd=tmp_path)

    @t.span("double")
    def double(x: int) -> int:
        return x * 2

    assert double(21) == 42
    spans = list(t.store.store.iter_spans())
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "double"
    assert span.output == 42
    assert span.input == {"args": [21], "kwargs": {}}
    assert span.status == SpanStatus.OK


def test_span_decorator_captures_exception(tmp_path: Path) -> None:
    """A decorated function that raises produces an ERROR span with the message."""
    t = Tracer(cwd=tmp_path)

    @t.span("boom")
    def boom() -> None:
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        boom()
    spans = list(t.store.store.iter_spans())
    assert len(spans) == 1
    assert spans[0].status == SpanStatus.ERROR
    assert spans[0].attributes["error.message"] == "kaboom"


def test_span_id_is_deterministic(tmp_path: Path) -> None:
    """Same input twice → same span id (content-addressed)."""
    t = Tracer(cwd=tmp_path)

    @t.span("idempotent")
    def f(x: int) -> int:
        return x

    f(5)
    f(5)
    spans = list(t.store.store.iter_spans())
    # Both calls produce the same span id; the store dedupes.
    assert len(spans) == 1


def test_nested_spans_share_trace(tmp_path: Path) -> None:
    """Nested spans share a trace id and parent correctly."""
    t = Tracer(cwd=tmp_path)

    @t.agent
    def outer() -> int:

        @t.span("inner")
        def inner() -> int:
            return 7

        return inner()

    assert outer() == 7
    trace_ids = list(t.store.store.iter_traces())
    assert len(trace_ids) == 1
    spans = list(t.store.store.iter_spans(trace_id=trace_ids[0]))
    assert len(spans) == 2
    names = sorted(s.name for s in spans)
    assert names == ["inner", "outer"]


def test_sync_and_async_both_work(tmp_path: Path) -> None:
    """The @span decorator handles sync and async callables."""
    t = Tracer(cwd=tmp_path)

    @t.span("sync")
    def sfn(x: int) -> int:
        return x + 1

    @t.span("async")
    async def afn(x: int) -> int:
        return x + 2

    assert sfn(1) == 2
    assert anyio.run(afn, 1) == 3
    spans = list(t.store.store.iter_spans())
    names = sorted(s.name for s in spans)
    assert names == ["async", "sync"]


def test_trace_context_manager(tmp_path: Path) -> None:
    """``with t.trace(...)`` records a span and exposes output/attribute setters."""
    t = Tracer(cwd=tmp_path)
    with t.trace("block", type=SpanType.TOOL) as span:
        span.set_attribute("k", "v")
        span.set_output("done")
    spans = list(t.store.store.iter_spans())
    assert len(spans) == 1
    s = spans[0]
    assert s.name == "block"
    assert s.type == SpanType.TOOL
    assert s.attributes.get("k") == "v"
    assert s.output == "done"


def test_lazy_store_creation(tmp_path: Path) -> None:
    """Tracer(store=None) creates a store at the given cwd."""
    cwd = tmp_path / "x"
    cwd.mkdir()
    t = Tracer(cwd=cwd)
    with t.trace("hello") as span:
        span.set_output("world")
    assert (cwd / ".clew").exists()


# ---------------------------------------------------------------------------
# Generator / async generator support
# ---------------------------------------------------------------------------


def test_sync_generator_span_captures_each_yield(tmp_path: Path) -> None:
    """A sync generator is wrapped: parent span + per-item child spans."""
    t = Tracer(cwd=tmp_path)

    @t.agent
    def run():
        gen = stream()
        return list(gen)

    @t.span("stream")
    def stream():
        for i in range(3):
            yield i * 10

    run()
    ts = TraceStore(Store(tmp_path / ".clew"))
    spans = list(ts.store.iter_spans())
    names = {s.name for s in spans}
    assert "run" in names
    assert "stream" in names
    item_names = {n for n in names if n.startswith("stream.item-")}
    assert item_names == {"stream.item-0", "stream.item-1", "stream.item-2"}
    # The parent span's output is the list of items.
    parent = next(s for s in spans if s.name == "stream")
    assert parent.output == [0, 10, 20]


def test_sync_generator_span_records_error(tmp_path: Path) -> None:
    """A generator that raises mid-iteration records the error."""
    t = Tracer(cwd=tmp_path)

    @t.agent
    def run():
        return list(broken())

    @t.span("broken")
    def broken():
        yield 1
        raise RuntimeError("kapow")

    with pytest.raises(RuntimeError, match="kapow"):
        run()
    ts = TraceStore(Store(tmp_path / ".clew"))
    spans = list(ts.store.iter_spans())
    parent = next(s for s in spans if s.name == "broken")
    assert parent.status == SpanStatus.ERROR
    assert "kapow" in (parent.error or "")


def test_async_generator_span_captures_each_yield(tmp_path: Path) -> None:
    """An async generator is wrapped with the same parent + child layout."""
    t = Tracer(cwd=tmp_path)

    @t.agent
    async def run():
        out = []
        async for x in astream():
            out.append(x)
        return out

    @t.span("astream")
    async def astream():
        for i in range(2):
            yield i * 100

    asyncio.run(run())
    ts = TraceStore(Store(tmp_path / ".clew"))
    spans = list(ts.store.iter_spans())
    names = {s.name for s in spans}
    assert "astream" in names
    assert "astream.item-0" in names
    assert "astream.item-1" in names


def test_async_generator_span_records_error(tmp_path: Path) -> None:
    t = Tracer(cwd=tmp_path)

    @t.agent
    async def run():
        out = []
        async for x in broken():
            out.append(x)
        return out

    @t.span("broken")
    async def broken():
        yield 1
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        asyncio.run(run())
    ts = TraceStore(Store(tmp_path / ".clew"))
    spans = list(ts.store.iter_spans())
    parent = next(s for s in spans if s.name == "broken")
    assert parent.status == SpanStatus.ERROR
    assert "nope" in (parent.error or "")
