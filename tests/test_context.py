"""Tests for the span context (task-local active span)."""

from __future__ import annotations

import anyio
import pytest

from clew.sdk.context import (
    current_span,
    current_trace_id,
    reset_current_span,
    set_current_span,
)


def test_current_span_is_none_at_start() -> None:
    """No active span at import time."""
    assert current_span() is None
    assert current_trace_id() is None


def test_set_and_reset_roundtrip() -> None:
    """set + reset returns to the previous value (None)."""
    sentinel = object()
    token = set_current_span(sentinel)
    assert current_span() is sentinel
    reset_current_span(token)
    assert current_span() is None


def test_set_replaces_previous() -> None:
    """Setting a new span without resetting the previous token does not affect what is observed."""
    first = object()
    second = object()
    token = set_current_span(first)
    assert current_span() is first
    # Set another; we should observe the new one.
    token2 = set_current_span(second)
    assert current_span() is second
    # Resetting the second token returns to the first (which is still set).
    reset_current_span(token2)
    assert current_span() is first
    # And then the first token returns to None.
    reset_current_span(token)
    assert current_span() is None


def test_current_trace_id_returns_none_when_no_span() -> None:
    """current_trace_id() is None when there's no active span."""
    assert current_trace_id() is None


def test_current_trace_id_reads_from_span() -> None:
    """When a span is current, current_trace_id() returns its trace_id."""

    class FakeSpan:
        trace_id = "trace-xyz"

    token = set_current_span(FakeSpan())
    try:
        assert current_trace_id() == "trace-xyz"
    finally:
        reset_current_span(token)


@pytest.mark.anyio
async def test_concurrent_tasks_have_isolated_contexts() -> None:
    """Different anyio tasks see different current_span() values."""

    async def child(label: str) -> str:
        token = set_current_span(label)
        await anyio.sleep(0.01)
        seen = current_span()
        reset_current_span(token)
        return str(seen)

    async with anyio.create_task_group() as tg:
        results: list[str] = []
        for i in range(5):

            async def task(i: int = i, results: list[str] = results) -> None:
                results.append(await child(f"task-{i}"))

            tg.start_soon(task)
    assert sorted(results) == [f"task-{i}" for i in range(5)]
