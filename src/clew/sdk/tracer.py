"""User-facing decorators and context managers for recording Clew spans."""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar, cast

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.sdk.context import current_span, reset_current_span, set_current_span

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(slots=True)
class _ActiveSpan:
    """Mutable execution state that can never be persisted directly."""

    id: str
    owner_id: str
    trace_id: str
    parent_ids: list[str]
    sequence: int
    type: SpanType
    name: str
    input: Any
    started_at: datetime
    attributes: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    metadata: dict[str, Any] | None = None


class Tracer:
    """Record finalized sync, async, generator, and context-manager spans."""

    def __init__(
        self,
        store: TraceStore | None = None,
        name: str = "default",
        cwd: Path | None = None,
    ) -> None:
        self.name = name
        self._context_id = uuid.uuid4().hex
        root = (cwd or Path.cwd()) / ".clew"
        self._store = store if store is not None else TraceStore(Store(root))
        self._sequence_lock = threading.Lock()
        self._next_sequence: dict[str, int] = {}
        self._manual_lock = threading.RLock()
        self._active_spans: dict[str, _ActiveSpan] = {}
        self._active_tokens: dict[str, Any] = {}

    @property
    def store(self) -> TraceStore:
        return self._store

    # -- trace roots ----------------------------------------------------

    def agent(self, fn: Callable[P, R]) -> Callable[P, R]:
        """Decorate the entry point of one independently identified trace."""
        if asyncio.iscoroutinefunction(fn):
            async_fn = cast(Callable[P, Awaitable[R]], fn)

            @functools.wraps(fn)
            async def async_agent(*args: P.args, **kwargs: P.kwargs) -> R:
                return await self._run_async_agent(async_fn, args, kwargs)

            return async_agent  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_agent(*args: P.args, **kwargs: P.kwargs) -> R:
            return self._run_sync_agent(fn, args, kwargs)

        return sync_agent

    def _run_sync_agent(
        self,
        fn: Callable[..., R],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> R:
        active = self._new_active(
            name=fn.__name__,
            type=SpanType.OBSERVATION,
            input={"args": list(args), "kwargs": kwargs},
            force_new_trace=True,
        )
        token = set_current_span(active)
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            self._store.add_span(self._finalize(active, error=exc))
            raise
        else:
            self._store.add_span(self._finalize(active, output=result))
            return result
        finally:
            reset_current_span(token)
            self._release_sequence(active)

    async def _run_async_agent(
        self,
        fn: Callable[..., Awaitable[R]],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> R:
        active = self._new_active(
            name=fn.__name__,
            type=SpanType.OBSERVATION,
            input={"args": list(args), "kwargs": kwargs},
            force_new_trace=True,
        )
        token = set_current_span(active)
        try:
            result = await fn(*args, **kwargs)
        except BaseException as exc:
            self._store.add_span(self._finalize(active, error=exc))
            raise
        else:
            self._store.add_span(self._finalize(active, output=result))
            return result
        finally:
            reset_current_span(token)
            self._release_sequence(active)

    # -- decorated child spans -----------------------------------------

    def span(
        self,
        name: str | None = None,
        type: SpanType = SpanType.OBSERVATION,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Record each call to the decorated function as one occurrence."""

        def decorator(fn: Callable[P, R]) -> Callable[P, R]:
            span_name = name or fn.__name__
            if inspect.isasyncgenfunction(fn):

                @functools.wraps(fn)
                async def async_gen_wrapped(
                    *args: P.args, **kwargs: P.kwargs
                ) -> AsyncIterator[Any]:
                    async for item in self._run_async_gen_span(fn, args, kwargs, span_name, type):
                        yield item

                return async_gen_wrapped  # type: ignore[return-value]

            if inspect.isgeneratorfunction(fn):

                @functools.wraps(fn)
                def sync_gen_wrapped(*args: P.args, **kwargs: P.kwargs) -> Iterator[Any]:
                    yield from self._run_sync_gen_span(fn, args, kwargs, span_name, type)

                return sync_gen_wrapped  # type: ignore[return-value]

            if asyncio.iscoroutinefunction(fn):
                async_fn = cast(Callable[P, Awaitable[R]], fn)

                @functools.wraps(fn)
                async def async_wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
                    return await self._run_async_span(async_fn, args, kwargs, span_name, type)

                return async_wrapped  # type: ignore[return-value]

            @functools.wraps(fn)
            def sync_wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
                return self._run_sync_span(fn, args, kwargs, span_name, type)

            return sync_wrapped

        return decorator

    def _run_sync_span(
        self,
        fn: Callable[..., R],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        name: str,
        type: SpanType,
    ) -> R:
        active = self._new_active(
            name=name, type=type, input={"args": list(args), "kwargs": kwargs}
        )
        token = set_current_span(active)
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            self._store.add_span(self._finalize(active, error=exc))
            raise
        else:
            self._store.add_span(self._finalize(active, output=result))
            return result
        finally:
            reset_current_span(token)
            self._release_sequence(active)

    async def _run_async_span(
        self,
        fn: Callable[..., Awaitable[R]],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        name: str,
        type: SpanType,
    ) -> R:
        active = self._new_active(
            name=name, type=type, input={"args": list(args), "kwargs": kwargs}
        )
        token = set_current_span(active)
        try:
            result = await fn(*args, **kwargs)
        except BaseException as exc:
            self._store.add_span(self._finalize(active, error=exc))
            raise
        else:
            self._store.add_span(self._finalize(active, output=result))
            return result
        finally:
            reset_current_span(token)
            self._release_sequence(active)

    def _run_sync_gen_span(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        name: str,
        type: SpanType,
    ) -> Iterator[Any]:
        active = self._new_active(
            name=name, type=type, input={"args": list(args), "kwargs": kwargs}
        )
        token = set_current_span(active)
        items: list[Any] = []
        try:
            for index, item in enumerate(fn(*args, **kwargs)):
                items.append(item)
                self._record_stream_item(active, index, item)
                yield item
        except BaseException as exc:
            self._store.add_span(self._finalize(active, error=exc))
            raise
        else:
            self._store.add_span(self._finalize(active, output=items))
        finally:
            reset_current_span(token)
            self._release_sequence(active)

    async def _run_async_gen_span(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        name: str,
        type: SpanType,
    ) -> AsyncIterator[Any]:
        active = self._new_active(
            name=name, type=type, input={"args": list(args), "kwargs": kwargs}
        )
        token = set_current_span(active)
        items: list[Any] = []
        try:
            index = 0
            async for item in fn(*args, **kwargs):
                items.append(item)
                self._record_stream_item(active, index, item)
                index += 1
                yield item
        except BaseException as exc:
            self._store.add_span(self._finalize(active, error=exc))
            raise
        else:
            self._store.add_span(self._finalize(active, output=items))
        finally:
            reset_current_span(token)
            self._release_sequence(active)

    def _record_stream_item(self, parent: _ActiveSpan, index: int, item: Any) -> None:
        child = self._new_active(
            name=f"{parent.name}.item-{index}",
            type=SpanType.OBSERVATION,
            input={"index": index},
            parent=parent,
        )
        self._store.add_span(self._finalize(child, output=item))

    # -- manual integration hooks --------------------------------------

    def _begin(
        self,
        name: str,
        type: SpanType = SpanType.OBSERVATION,
        *,
        input: Any = None,
        attributes: dict[str, Any] | None = None,
        parent: _ActiveSpan | Span | None = None,
        activate_context: bool = True,
    ) -> _ActiveSpan:
        """Open internal mutable state for event-driven integrations."""
        active = self._new_active(name=name, type=type, input=input, parent=parent)
        if attributes:
            active.attributes.update(attributes)
        with self._manual_lock:
            self._active_spans[active.id] = active
            if activate_context:
                self._active_tokens[active.id] = set_current_span(active)
        return active

    def _end(
        self,
        span_id: str,
        *,
        output: Any = None,
        error: BaseException | None = None,
    ) -> Span | None:
        """Finalize internal state created by :meth:`_begin`."""
        with self._manual_lock:
            active = self._active_spans.pop(span_id, None)
            token = self._active_tokens.pop(span_id, None)
        if active is None:
            return None
        finalized = self._finalize(active, output=output, error=error)
        try:
            self._store.add_span(finalized)
        finally:
            if token is not None:
                reset_current_span(token)
            self._release_sequence(active)
        return finalized

    # -- context manager ------------------------------------------------

    def trace(
        self,
        name: str,
        type: SpanType = SpanType.OBSERVATION,
    ) -> _TraceContext:
        return _TraceContext(self, name, type)

    # -- construction ---------------------------------------------------

    def _allocate_sequence(self, trace_id: str) -> int:
        with self._sequence_lock:
            sequence = self._next_sequence.get(trace_id, 0)
            self._next_sequence[trace_id] = sequence + 1
            return sequence

    def _release_sequence(self, active: _ActiveSpan) -> None:
        if active.parent_ids:
            return
        with self._sequence_lock:
            self._next_sequence.pop(active.trace_id, None)

    def _new_active(
        self,
        *,
        name: str,
        type: SpanType,
        input: Any,
        force_new_trace: bool = False,
        parent: _ActiveSpan | Span | None = None,
    ) -> _ActiveSpan:
        resolved_parent: _ActiveSpan | Span | None
        if force_new_trace:
            resolved_parent = None
        elif parent is not None:
            resolved_parent = parent
        else:
            candidate = current_span()
            # ContextVars are process-global so async tasks inherit nesting, but
            # two independent Tracer instances must never write each other's
            # payloads or parent ids into different stores.
            resolved_parent = (
                candidate
                if isinstance(candidate, _ActiveSpan) and candidate.owner_id == self._context_id
                else None
            )
        trace_id = resolved_parent.trace_id if resolved_parent is not None else uuid.uuid4().hex
        parent_ids = [resolved_parent.id] if resolved_parent is not None else []
        return _ActiveSpan(
            id=uuid.uuid4().hex,
            owner_id=self._context_id,
            trace_id=trace_id,
            parent_ids=parent_ids,
            sequence=self._allocate_sequence(trace_id),
            type=type,
            name=name,
            input=input,
            started_at=datetime.now(UTC),
        )

    @staticmethod
    def _finalize(
        active: _ActiveSpan,
        output: Any = None,
        error: BaseException | None = None,
        *,
        status: SpanStatus | None = None,
    ) -> Span:
        attributes = dict(active.attributes)
        final_status = status or (SpanStatus.ERROR if error is not None else SpanStatus.OK)
        error_text: str | None = None
        if error is not None:
            attributes["error.type"] = type(error).__name__
            attributes["error.message"] = str(error)
            error_text = f"{type(error).__name__}: {error}"
        return Span(
            id=active.id,
            trace_id=active.trace_id,
            parent_ids=list(active.parent_ids),
            sequence=active.sequence,
            type=active.type,
            name=active.name,
            attributes=attributes,
            input=active.input,
            output=output,
            started_at=active.started_at,
            ended_at=datetime.now(UTC),
            status=final_status,
            error=error_text,
            metadata=active.metadata,
        )


class _TraceContext:
    """Mutable facade for a context-managed internal active span."""

    def __init__(self, tracer: Tracer, name: str, type: SpanType) -> None:
        self._tracer = tracer
        self._name = name
        self._type = type
        self._active: _ActiveSpan | None = None
        self._token: Any = None

    def __enter__(self) -> _TraceContext:
        self._active = self._tracer._new_active(name=self._name, type=self._type, input=None)
        self._token = set_current_span(self._active)
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        if self._active is None:
            raise RuntimeError("Clew trace context exited before it was entered")
        finalized = self._tracer._finalize(self._active, output=self._active.output, error=exc)
        try:
            self._tracer.store.add_span(finalized)
        finally:
            if self._token is not None:
                reset_current_span(self._token)
            self._tracer._release_sequence(self._active)

    def set_output(self, output: Any) -> None:
        if self._active is None:
            raise RuntimeError("set_output called outside `with` block")
        self._active.output = output

    def set_input(self, input: Any) -> None:
        if self._active is None:
            raise RuntimeError("set_input called outside `with` block")
        self._active.input = input

    def set_attribute(self, key: str, value: Any) -> None:
        if self._active is None:
            raise RuntimeError("set_attribute called outside `with` block")
        self._active.attributes[key] = value


__all__ = ["Tracer"]
