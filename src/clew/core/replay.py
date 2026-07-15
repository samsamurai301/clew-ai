"""Topology-safe replay into a new, independently identified trace."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from clew.core.models import Span, SpanStatus, Trace
from clew.core.trace import TraceStore


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """The constrained payload an executor may return to the replay engine."""

    output: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: SpanStatus = SpanStatus.OK
    error: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status is SpanStatus.SKIPPED:
            raise ValueError("Executors cannot return SKIPPED; only the engine can skip.")
        if self.status is SpanStatus.ERROR and not self.error:
            raise ValueError("An ERROR ReplayResult must include an error message.")
        if self.status is not SpanStatus.ERROR and self.error:
            raise ValueError("Only an ERROR ReplayResult may include an error message.")


@dataclass(frozen=True, slots=True)
class ReplayContext:
    """Finalized parent context supplied to a replay executor."""

    model: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)
    parent_chain: tuple[Span, ...] = ()


@runtime_checkable
class ReplayExecutor(Protocol):
    """A sync or async callable object that returns only replay payload data."""

    def execute(
        self, span: Span, context: ReplayContext
    ) -> ReplayResult | Awaitable[ReplayResult]: ...


class MockExecutor:
    """Offline executor that reuses the captured output and attributes."""

    def execute(self, span: Span, context: ReplayContext) -> ReplayResult:
        del context
        return ReplayResult(output=span.output)


class RecordingExecutor:
    """Adapt a ``(Span, ReplayContext)`` callable to ReplayExecutor."""

    def __init__(
        self,
        fn: Callable[[Span, ReplayContext], ReplayResult | Awaitable[ReplayResult]],
    ) -> None:
        self._fn = fn

    def execute(self, span: Span, context: ReplayContext) -> ReplayResult | Awaitable[ReplayResult]:
        return self._fn(span, context)


class ReplayEngine:
    """Replay a full trace or a selected subtree without cross-trace parents."""

    def __init__(self, store: TraceStore, executor: ReplayExecutor | None = None) -> None:
        self._store = store
        self._executor = executor or MockExecutor()

    @property
    def executor(self) -> ReplayExecutor:
        return self._executor

    def dry_run(self, trace_id: str, from_span_id: str | None = None) -> list[Span]:
        """Return only spans that would be executed, not cloned ancestors."""
        trace = self._store.get_trace(trace_id)
        if from_span_id is None:
            return list(trace.spans)
        by_id = {span.id: span for span in trace.spans}
        if from_span_id not in by_id:
            raise KeyError(f"span {from_span_id!r} not in trace {trace_id!r}")
        selected = {from_span_id}
        changed = True
        while changed:
            changed = False
            for span in trace.spans:
                if span.id not in selected and any(
                    parent in selected for parent in span.parent_ids
                ):
                    selected.add(span.id)
                    changed = True
        return [span for span in trace.spans if span.id in selected]

    async def replay(
        self,
        trace_id: str,
        from_span_id: str | None = None,
        executor: ReplayExecutor | None = None,
    ) -> Trace:
        """Persist and return a complete diagnostic replay trace.

        Executor exceptions become ERROR spans. Every descendant that depends
        on a failed occurrence becomes SKIPPED, so the returned trace remains
        inspectable and structurally valid.
        """
        selected_executor = executor or self._executor
        original = self._store.get_trace(trace_id)
        by_id = {span.id: span for span in original.spans}
        run_ids = {span.id for span in self.dry_run(trace_id, from_span_id)}
        included_ids = set(run_ids)
        # A selected descendant can be a multi-parent join. Clone the complete
        # ancestor closure for every selected node, not just the target's first
        # lineage, so all rewritten parent ids belong to the new trace.
        frontier = list(included_ids)
        while frontier:
            current = by_id[frontier.pop()]
            for parent_id in current.parent_ids:
                if parent_id not in included_ids:
                    included_ids.add(parent_id)
                    frontier.append(parent_id)

        included = [span for span in original.spans if span.id in included_ids]
        new_trace_id = uuid.uuid4().hex
        # Allocate every identity before execution. Parent rewriting therefore
        # cannot accidentally retain an old-trace id, even for partial replay.
        id_map = {span.id: uuid.uuid4().hex for span in included}
        new_by_old_id: dict[str, Span] = {}
        failed_or_skipped: set[str] = set()

        for sequence, source in enumerate(included):
            new_parents = [id_map[parent] for parent in source.parent_ids]
            if source.id not in run_ids:
                finalized = self._clone(
                    source,
                    id=id_map[source.id],
                    trace_id=new_trace_id,
                    parent_ids=new_parents,
                    sequence=sequence,
                )
            elif any(parent in failed_or_skipped for parent in source.parent_ids):
                failed_parents = [
                    parent for parent in source.parent_ids if parent in failed_or_skipped
                ]
                finalized = self._from_result(
                    source,
                    ReplayResult(
                        output=None,
                        attributes={
                            "replay.skip_reason": "dependency failed",
                            "replay.failed_parent_ids": failed_parents,
                        },
                    ),
                    id=id_map[source.id],
                    trace_id=new_trace_id,
                    parent_ids=new_parents,
                    sequence=sequence,
                    started_at=datetime.now(UTC),
                    ended_at=datetime.now(UTC),
                    forced_status=SpanStatus.SKIPPED,
                )
                failed_or_skipped.add(source.id)
            else:
                parent_chain = self._parent_chain(source, by_id, new_by_old_id)
                context = ReplayContext(parent_chain=tuple(parent_chain))
                started_at = datetime.now(UTC)
                try:
                    result = selected_executor.execute(source, context)
                    if inspect.isawaitable(result):
                        result = await result
                    if not isinstance(result, ReplayResult):
                        raise TypeError(
                            "Replay executors must return ReplayResult, got "
                            f"{type(result).__name__}."
                        )
                    ended_at = datetime.now(UTC)
                    finalized = self._from_result(
                        source,
                        result,
                        id=id_map[source.id],
                        trace_id=new_trace_id,
                        parent_ids=new_parents,
                        sequence=sequence,
                        started_at=started_at,
                        ended_at=ended_at,
                    )
                    if result.status is SpanStatus.ERROR:
                        failed_or_skipped.add(source.id)
                except Exception as exc:
                    ended_at = datetime.now(UTC)
                    finalized = self._from_result(
                        source,
                        ReplayResult(
                            output=None,
                            attributes={
                                "error.type": type(exc).__name__,
                                "error.message": str(exc),
                            },
                            status=SpanStatus.ERROR,
                            error=f"{type(exc).__name__}: {exc}",
                        ),
                        id=id_map[source.id],
                        trace_id=new_trace_id,
                        parent_ids=new_parents,
                        sequence=sequence,
                        started_at=started_at,
                        ended_at=ended_at,
                    )
                    failed_or_skipped.add(source.id)
            self._store.add_span(finalized)
            new_by_old_id[source.id] = finalized

        return self._store.get_trace(new_trace_id)

    def _replay_sync(
        self,
        trace_id: str,
        executor: ReplayExecutor | None = None,
        from_span_id: str | None = None,
    ) -> Trace:
        """Drive :meth:`replay` when no event loop is currently running."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.replay(trace_id, from_span_id, executor))
        raise RuntimeError(
            "ReplayEngine._replay_sync cannot run inside an active event loop; "
            "await ReplayEngine.replay(...) instead."
        )

    @staticmethod
    def _parent_chain(
        source: Span,
        source_by_id: dict[str, Span],
        finalized_by_old_id: dict[str, Span],
    ) -> list[Span]:
        ancestor_ids: set[str] = set()
        frontier = list(source.parent_ids)
        while frontier:
            parent_id = frontier.pop()
            if parent_id in ancestor_ids:
                continue
            ancestor_ids.add(parent_id)
            frontier.extend(source_by_id[parent_id].parent_ids)
        return sorted(
            (finalized_by_old_id[parent_id] for parent_id in ancestor_ids),
            key=lambda span: (span.sequence, span.id),
        )

    @staticmethod
    def _clone(
        source: Span,
        *,
        id: str,
        trace_id: str,
        parent_ids: list[str],
        sequence: int,
    ) -> Span:
        return Span(
            id=id,
            trace_id=trace_id,
            parent_ids=parent_ids,
            sequence=sequence,
            type=source.type,
            name=source.name,
            attributes=dict(source.attributes),
            input=source.input,
            output=source.output,
            started_at=source.started_at,
            ended_at=source.ended_at,
            status=source.status,
            error=source.error,
            metadata=dict(source.metadata) if source.metadata is not None else None,
        )

    @staticmethod
    def _from_result(
        source: Span,
        result: ReplayResult,
        *,
        id: str,
        trace_id: str,
        parent_ids: list[str],
        sequence: int,
        started_at: datetime,
        ended_at: datetime,
        forced_status: SpanStatus | None = None,
    ) -> Span:
        attributes = dict(source.attributes)
        attributes.update(result.attributes)
        status = forced_status or result.status
        return Span(
            id=id,
            trace_id=trace_id,
            parent_ids=parent_ids,
            sequence=sequence,
            type=source.type,
            name=source.name,
            attributes=attributes,
            input=source.input,
            output=result.output,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            error=result.error if status is SpanStatus.ERROR else None,
            metadata=result.metadata,
        )


__all__ = [
    "MockExecutor",
    "RecordingExecutor",
    "ReplayContext",
    "ReplayEngine",
    "ReplayExecutor",
    "ReplayResult",
]
