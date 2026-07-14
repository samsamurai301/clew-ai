"""Replay engine: re-execute a recorded trace, optionally from any span.

A *replay* walks a recorded trace and re-runs each span through a
user-supplied :class:`ReplayExecutor`. Crucially, replay **never
mutates the original trace** — it always produces a new trace (with
a fresh ``trace_id``) that the caller can compare against the
original.

Two built-in executors ship with clew:

* :class:`MockExecutor` — re-uses the recorded output, byte-for-byte.
  This is the default; it gives you a deterministic, side-effect-free
  way to verify that a trace can be replayed at all.
* :class:`RecordingExecutor` — calls a user-supplied async function
  to compute fresh outputs and records them into the new trace.

If ``from_span_id`` is given, only that span and its descendants are
re-executed; ancestors are copied verbatim from the original.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from clew.core.models import Span, SpanStatus, Trace
from clew.core.trace import TraceStore

# A 64-char hex string placeholder for "no parent" in a fresh trace.
_NULL_PARENT: str = "0" * 64


@dataclass
class ReplayContext:
    """Inputs to a replay run, passed to :class:`ReplayExecutor.execute`."""

    model: str = ""
    """LLM model name (or empty for non-LLM spans)."""

    params: dict[str, Any] = field(default_factory=dict)
    """Free-form execution parameters (temperature, tools, etc.)."""

    env: dict[str, Any] = field(default_factory=dict)
    """Environment / secrets (mocked; real values must never be replayed)."""

    parent_chain: list[Span] = field(default_factory=list)
    """Ancestor spans (root first) — the executor can use these for context."""


@runtime_checkable
class ReplayExecutor(Protocol):
    """Protocol a replay executor must satisfy."""

    async def execute(self, span: Span, ctx: ReplayContext) -> Span:
        """Re-execute ``span`` under ``ctx`` and return the resulting span.

        Implementations MUST return a new :class:`Span` (never mutate
        the input). The returned span should be ``content_hash``-stable
        for identical inputs to be considered deterministic.
        """
        ...


class MockExecutor:
    """Replay executor that re-uses the recorded output verbatim.

    Useful for testing the replay pipeline itself without depending
    on any external model or tool. The replayed trace is bit-identical
    to the original (same span ids, same content hashes).
    """

    async def execute(self, span: Span, ctx: ReplayContext) -> Span:
        """Return a fresh copy of ``span`` with the original output.

        The returned span has the same id, same parent ids, same
        input/output/attributes — the only thing that changes is
        the ``started_at``/``ended_at`` timestamps (set to now).
        """
        return self.execute_sync(span, ctx)

    def execute_sync(self, span: Span, ctx: ReplayContext) -> Span:
        """Synchronous variant of :meth:`execute`.

        Used by :class:`ReplayEngine._replay_sync`, which runs from
        contexts (CLI commands, MCP tool handlers) that cannot
        drive a coroutine because an event loop is already
        running.
        """
        now = datetime.now(UTC)
        return span.model_copy(update={"started_at": now, "ended_at": now, "status": SpanStatus.OK})


class RecordingExecutor:
    """Replay executor that calls a user function to compute outputs.

    The callable receives ``(span, ctx)`` and returns a tuple of
    ``(output, attributes_delta)``. The new span inherits the
    original's input, name, and parent ids; output, status, and
    ended_at are set from the callable's return value.
    """

    def __init__(self, fn: Any) -> None:
        """Wrap a callable ``async def fn(span, ctx) -> (output, attributes_delta)``."""
        self._fn = fn

    async def execute(self, span: Span, ctx: ReplayContext) -> Span:
        """Call the wrapped function and return the resulting span."""
        output, attrs_delta = await self._fn(span, ctx)
        now = datetime.now(UTC)
        new_attrs = dict(span.attributes)
        new_attrs.update(attrs_delta or {})
        return span.model_copy(
            update={
                "output": output,
                "attributes": new_attrs,
                "ended_at": now,
                "status": SpanStatus.OK,
            }
        )


class ReplayEngine:
    """Walk a recorded trace and produce a fresh trace via an executor."""

    def __init__(self, store: TraceStore, executor: ReplayExecutor | None = None) -> None:
        """Attach to a store and pick a default executor.

        The default executor is :class:`MockExecutor` if none is given.
        """
        self._store = store
        self._executor: ReplayExecutor = executor if executor is not None else MockExecutor()

    @property
    def executor(self) -> ReplayExecutor:
        """Return the configured executor."""
        return self._executor

    def dry_run(self, trace_id: str, from_span_id: str | None = None) -> list[Span]:
        """Return the spans that ``replay()`` would re-execute.

        Does not write anything. The returned list is in topological
        order (parents before children).
        """
        original = self._store.get_trace(trace_id)
        if from_span_id is None:
            return list(original.spans)
        descendants = {from_span_id}
        frontier = [from_span_id]
        while frontier:
            current = frontier.pop()
            for s in original.spans:
                if current in s.parent_ids and s.id not in descendants:
                    descendants.add(s.id)
                    frontier.append(s.id)
        return [s for s in original.spans if s.id in descendants]

    async def replay(
        self,
        trace_id: str,
        from_span_id: str | None = None,
        executor: ReplayExecutor | None = None,
    ) -> Trace:
        """Re-execute a trace, producing a new trace with a fresh id.

        The original trace is never mutated. The new trace shares
        the original's content (input, attributes) but has new
        ``trace_id`` and re-executed ``output``/``status``/timing.

        If ``from_span_id`` is None, replay from the root. Otherwise,
        replay that span and its descendants; ancestors are copied.
        """
        ex = executor if executor is not None else self._executor
        original = self._store.get_trace(trace_id)
        new_trace_id = uuid4().hex
        new_spans: list[Span] = []
        # Map original span id -> new span id so we can rewrite parents.
        id_map: dict[str, str] = {}
        # We always pick a brand-new root for the replayed trace.
        spans_to_run: list[Span]
        ancestors_to_copy: list[Span]
        if from_span_id is None:
            spans_to_run = list(original.spans)
            ancestors_to_copy = []
        else:
            # Verify the span exists in the trace.
            if not any(s.id == from_span_id for s in original.spans):
                raise KeyError(f"span {from_span_id!r} not in trace {trace_id!r}")
            run_set_ids = {s.id for s in self.dry_run(trace_id, from_span_id)}
            spans_to_run = [s for s in original.spans if s.id in run_set_ids]
            ancestors_to_copy = [s for s in original.spans if s.id not in run_set_ids]
        # First pass: create new spans (with placeholder content for the
        # ones we'll re-execute), writing them to the store. The store
        # returns their (possibly deduplicated) ids.
        for span in spans_to_run:
            new_id = uuid4().hex
            id_map[span.id] = new_id
        # Now rewrite parents and re-execute.
        for span in spans_to_run:
            new_parents = [id_map.get(p, p) for p in span.parent_ids]
            ctx = ReplayContext(
                parent_chain=[s for s in spans_to_run if s.id in new_parents],
            )
            new_span = await ex.execute(span, ctx)
            # Re-stamp with the new id, new trace, and rewritten parents.
            # Use the executor's ended_at if it set one; otherwise use
            # its started_at so we never violate the started_at <= ended_at
            # invariant enforced by the Span model.
            ended = new_span.ended_at or new_span.started_at
            rewritten = new_span.model_copy(
                update={
                    "id": id_map[span.id],
                    "trace_id": new_trace_id,
                    "parent_ids": new_parents,
                    "started_at": ended,
                    "ended_at": ended,
                }
            )
            # If the executor left the id as the original, the store will
            # dedupe; we need to ensure the new id is used. So we set it
            # here, and the store will accept it (idempotent on content).
            self._store.add_span(rewritten)
            new_spans.append(rewritten)
        # Copy ancestors verbatim. They get new ids (so the new trace
        # has a unique span identity) but identical content.
        for span in ancestors_to_copy:
            new_id = uuid4().hex
            id_map[span.id] = new_id
            new_parents = [id_map.get(p, p) for p in span.parent_ids]
            rewritten = span.model_copy(
                update={
                    "id": new_id,
                    "trace_id": new_trace_id,
                    "parent_ids": new_parents,
                }
            )
            self._store.add_span(rewritten)
            new_spans.append(rewritten)
        # Re-fetch the trace from the store so the Trace object is
        # exactly what the store holds (with the store's chosen ids).
        return self._store.get_trace(new_trace_id)

    def _replay_sync(
        self,
        trace_id: str,
        executor: ReplayExecutor | None = None,
    ) -> Trace:
        """Synchronous version of :meth:`replay`.

        For executors whose ``execute`` is a regular method (not a
        coroutine), this avoids the overhead of spinning up an event
        loop. Useful for CLI commands and MCP tool handlers that are
        already inside a running loop.
        """
        ex = executor if executor is not None else self._executor
        original = self._store.get_trace(trace_id)
        new_trace_id = uuid4().hex
        id_map: dict[str, str] = {}
        for span in original.spans:
            id_map[span.id] = uuid4().hex
        for span in original.spans:
            new_parents = [id_map.get(p, p) for p in span.parent_ids]
            ctx = ReplayContext(
                parent_chain=[s for s in original.spans if s.id in new_parents],
            )
            # Drive the executor synchronously. Most executors
            # (including :class:`MockExecutor`) expose a sync
            # ``execute_sync`` method. If the executor doesn't, we
            # attempt the async path via ``asyncio.run`` — this only
            # works if no event loop is already running.
            sync_attr = getattr(ex, "execute_sync", None)
            if sync_attr is not None:
                result = sync_attr(span, ctx)
            else:
                import asyncio
                import inspect
                maybe_coro = ex.execute(span, ctx)
                if inspect.iscoroutine(maybe_coro):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            raise TypeError(
                                "ReplayEngine._replay_sync cannot drive "
                                "an async executor from within a running "
                                "event loop; use ReplayEngine.replay "
                                "from an async context instead."
                            )
                    except RuntimeError:
                        pass
                    result = asyncio.run(maybe_coro)
                else:
                    result = maybe_coro
            ended = result.ended_at or result.started_at
            rewritten = result.model_copy(
                update={
                    "id": id_map[span.id],
                    "trace_id": new_trace_id,
                    "parent_ids": new_parents,
                    "started_at": ended,
                    "ended_at": ended,
                }
            )
            self._store.add_span(rewritten)
        return self._store.get_trace(new_trace_id)

