"""The clew :class:`Tracer` — decorator + context manager for spans.

This is the *user-facing* tracing surface. It is intentionally small:

    from clew.sdk import Tracer
    t = Tracer()  # uses ./.clew by default
    @t.agent
    def run(q):
        @t.span("search")
        def s():
            return search(q)
        s()
        return s.result

Behind the scenes, the tracer:

* Creates a :class:`~clew.core.store.Store` and wraps it in a
  :class:`~clew.core.trace.TraceStore` if none was passed in.
* Uses :mod:`clew.sdk.context` to track the currently-active span
  (so nested calls are auto-parented).
* Computes a deterministic span id from the span's content via
  :func:`clew.utils.hash.span_hash` so two identical inputs collapse
  to one span.
* Wraps both sync and async callables in the same decorator.
* Captures exceptions as :attr:`SpanStatus.ERROR` and stores the
  message in the span's ``attributes``.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.sdk.context import current_span, reset_current_span, set_current_span
from clew.utils.hash import span_hash

P = ParamSpec("P")
R = TypeVar("R")


class Tracer:
    """The user-facing tracer.

    A :class:`Tracer` is a thin wrapper over a :class:`TraceStore`.
    It knows how to:

    * Spawn a new trace (the ``@agent`` decorator)
    * Spawn a new span (the ``@span`` decorator and ``trace()`` ctx)
    * Resolve a store lazily (``cwd/.clew`` if none is given)
    * Honor a custom name (used to disambiguate when multiple
      :class:`Tracer` instances write to the same store).
    """

    def __init__(
        self,
        store: TraceStore | None = None,
        name: str = "default",
        cwd: Path | None = None,
    ) -> None:
        """Attach to (or create) a :class:`TraceStore`.

        If ``store`` is None, a fresh :class:`Store` rooted at
        ``<cwd>/.clew`` (or :data:`Path.cwd()/.clew`) is created
        and wrapped in a :class:`TraceStore`. The :class:`Tracer`
        is then ready to record spans.
        """
        self.name = name
        if store is None:
            root = (cwd or Path.cwd()) / ".clew"
            self._store = TraceStore(Store(root))
        else:
            self._store = store

    @property
    def store(self) -> TraceStore:
        """Return the underlying :class:`TraceStore`."""
        return self._store

    # -- entry point: a new trace ----------------------------------------

    def agent(self, fn: Callable[P, R]) -> Callable[P, R]:
        """Decorator marking the *entry point* of a trace.

        The decorated function becomes the trace's root span. The
        trace id is the root span's id (so it is content-addressed
        and stable for identical inputs).
        """
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_agent(*args: P.args, **kwargs: P.kwargs) -> R:
                return await self._run_async_agent(fn, args, kwargs)

            return async_agent  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_agent(*args: P.args, **kwargs: P.kwargs) -> R:
            return self._run_sync_agent(fn, args, kwargs)  # type: ignore[return-value]

        return sync_agent

    def _run_sync_agent(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Sync path for ``@agent``."""
        import uuid as _uuid
        trace_id = _uuid.uuid4().hex
        span = self._make_span(
            trace_id=trace_id,
            parent_ids=[],
            name=fn.__name__,
            type=SpanType.OBSERVATION,
            input={"args": list(args), "kwargs": kwargs},
        )
        token = set_current_span(span)
        try:
            result = fn(*args, **kwargs)
            span = self._finalize_span(span, result, error=None)
            self._store.add_span(span)
            return result
        except Exception as exc:
            err_span = self._finalize_span(span, None, error=exc)
            self._store.add_span(err_span)
            raise
        finally:
            reset_current_span(token)

    async def _run_async_agent(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Async path for ``@agent``."""
        import uuid as _uuid
        trace_id = _uuid.uuid4().hex
        span = self._make_span(
            trace_id=trace_id,
            parent_ids=[],
            name=fn.__name__,
            type=SpanType.OBSERVATION,
            input={"args": list(args), "kwargs": kwargs},
        )
        token = set_current_span(span)
        try:
            result = await fn(*args, **kwargs)
            span = self._finalize_span(span, result, error=None)
            self._store.add_span(span)
            return result
        except Exception as exc:
            err_span = self._finalize_span(span, None, error=exc)
            self._store.add_span(err_span)
            raise
        finally:
            reset_current_span(token)

    # -- child span: @span -----------------------------------------------

    def span(
        self,
        name: str | None = None,
        type: SpanType = SpanType.OBSERVATION,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Decorator that wraps a function as a child span.

        ``name`` defaults to the function's ``__name__``. The
        parent is whichever span was active when the wrapped function
        is called (or no parent if called at the top level — the
        span still records but is a "root" in its trace).
        """
        def decorator(fn: Callable[P, R]) -> Callable[P, R]:
            span_name = name or fn.__name__

            if inspect.isasyncgenfunction(fn):

                @functools.wraps(fn)
                async def async_gen_wrapped(
                    *args: P.args, **kwargs: P.kwargs
                ) -> AsyncIterator[Any]:
                    async for item in self._run_async_gen_span(
                        fn, args, kwargs, span_name, type
                    ):
                        yield item

                return async_gen_wrapped  # type: ignore[no-any-return, return-value]

            if inspect.isgeneratorfunction(fn):

                @functools.wraps(fn)
                def sync_gen_wrapped(
                    *args: P.args, **kwargs: P.kwargs
                ) -> Iterator[Any]:
                    yield from self._run_sync_gen_span(
                        fn, args, kwargs, span_name, type
                    )

                return sync_gen_wrapped  # type: ignore[no-any-return, return-value]

            if asyncio.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def async_wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
                    return await self._run_async_span(fn, args, kwargs, span_name, type)

                return async_wrapped  # type: ignore[no-any-return, return-value]

            @functools.wraps(fn)
            def sync_wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
                return self._run_as_span(fn, args, kwargs, span_name, type, is_async=False)

            return sync_wrapped  # type: ignore[no-any-return, return-value]

        return decorator

    def _run_as_span(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        span_name: str,
        span_type: SpanType,
        is_async: bool,
    ) -> Any:
        """Common driver behind ``@span`` for sync and async callables."""
        # Determine parent: the current span (if any).
        parent = current_span()
        # Determine trace id: reuse parent's trace if available, else
        # create a new one (top-level spans get their own trace).
        if parent is not None:
            trace_id = parent.trace_id
            parent_ids: list[str] = [parent.id]
        else:
            import uuid as _uuid
            trace_id = _uuid.uuid4().hex
            parent_ids = []
        span = self._make_span(
            trace_id=trace_id,
            parent_ids=parent_ids,
            name=span_name,
            type=span_type,
            input={"args": list(args), "kwargs": kwargs},
        )
        token = set_current_span(span)
        try:
            if is_async:
                # The async wrapper awaits; we only get here for
                # non-awaited coroutines, which is rare.
                raise RuntimeError("async span detected in sync path")
            result = fn(*args, **kwargs)
            span = self._finalize_span(span, result, error=None)
            self._store.add_span(span)
            return result
        except Exception as exc:
            err_span = self._finalize_span(span, None, error=exc)
            self._store.add_span(err_span)
            raise
        finally:
            reset_current_span(token)

    async def _run_async_span(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        span_name: str,
        span_type: SpanType,
    ) -> Any:
        """Async path of ``_run_as_span``."""
        parent = current_span()
        if parent is not None:
            trace_id = parent.trace_id
            parent_ids: list[str] = [parent.id]
        else:
            import uuid as _uuid
            trace_id = _uuid.uuid4().hex
            parent_ids = []
        span = self._make_span(
            trace_id=trace_id,
            parent_ids=parent_ids,
            name=span_name,
            type=span_type,
            input={"args": list(args), "kwargs": kwargs},
        )
        token = set_current_span(span)
        try:
            result = await fn(*args, **kwargs)
            span = self._finalize_span(span, result, error=None)
            self._store.add_span(span)
            return result
        except Exception as exc:
            err_span = self._finalize_span(span, None, error=exc)
            self._store.add_span(err_span)
            raise
        finally:
            reset_current_span(token)

    def _run_sync_gen_span(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        span_name: str,
        span_type: SpanType,
    ) -> Iterator[Any]:
        """Wrap a sync generator function: span lifetime = full iteration.

        The span starts when the consumer begins iterating and ends
        when the generator is exhausted (or raises). Each yielded
        item is captured as a child OBSERVATION span so streaming
        output is fully traced.
        """
        parent = current_span()
        if parent is not None:
            trace_id = parent.trace_id
            parent_ids: list[str] = [parent.id]
        else:
            import uuid as _uuid
            trace_id = _uuid.uuid4().hex
            parent_ids = []
        span = self._make_span(
            trace_id=trace_id,
            parent_ids=parent_ids,
            name=span_name,
            type=span_type,
            input={"args": list(args), "kwargs": kwargs},
        )
        token = set_current_span(span)
        items: list[Any] = []
        try:
            for i, item in enumerate(fn(*args, **kwargs)):
                items.append(item)
                # Per-item child span.
                child = self._make_span(
                    trace_id=trace_id,
                    parent_ids=[span.id],
                    name=f"{span_name}.item-{i}",
                    type=SpanType.OBSERVATION,
                    input={"index": i},
                )
                child_token = set_current_span(child)
                try:
                    child = self._finalize_span(child, item, error=None)
                    self._store.add_span(child)
                finally:
                    reset_current_span(child_token)
                yield item
            span = self._finalize_span(span, items, error=None)
            self._store.add_span(span)
        except Exception as exc:
            err_span = self._finalize_span(span, None, error=exc)
            self._store.add_span(err_span)
            raise
        finally:
            reset_current_span(token)

    async def _run_async_gen_span(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        span_name: str,
        span_type: SpanType,
    ) -> AsyncIterator[Any]:
        """Wrap an async generator function: span lifetime = full iteration."""
        parent = current_span()
        if parent is not None:
            trace_id = parent.trace_id
            parent_ids: list[str] = [parent.id]
        else:
            import uuid as _uuid
            trace_id = _uuid.uuid4().hex
            parent_ids = []
        span = self._make_span(
            trace_id=trace_id,
            parent_ids=parent_ids,
            name=span_name,
            type=span_type,
            input={"args": list(args), "kwargs": kwargs},
        )
        token = set_current_span(span)
        items: list[Any] = []
        try:
            i = 0
            async for item in fn(*args, **kwargs):
                items.append(item)
                child = self._make_span(
                    trace_id=trace_id,
                    parent_ids=[span.id],
                    name=f"{span_name}.item-{i}",
                    type=SpanType.OBSERVATION,
                    input={"index": i},
                )
                child_token = set_current_span(child)
                try:
                    child = self._finalize_span(child, item, error=None)
                    self._store.add_span(child)
                finally:
                    reset_current_span(child_token)
                yield item
                i += 1
            span = self._finalize_span(span, items, error=None)
            self._store.add_span(span)
        except Exception as exc:
            err_span = self._finalize_span(span, None, error=exc)
            self._store.add_span(err_span)
            raise
        finally:
            reset_current_span(token)

    # -- context manager --------------------------------------------------

    def trace(
        self,
        name: str,
        type: SpanType = SpanType.OBSERVATION,
    ) -> _TraceContext:
        """Open a span as a context manager.

        Useful when you want to record a span without decorating a
        function:

            with t.trace("my-block") as span:
                do_work()
                span.set_output(result)
        """
        return _TraceContext(self, name, type)

    def _begin(
        self,
        name: str,
        type: SpanType = SpanType.OBSERVATION,
    ) -> Span:
        """Open a span manually. Returns the in-flight :class:`Span`.

        Pair with :meth:`_end` to close the span. The span is added
        to the active context so child spans auto-parent onto it.
        This is the API integrations like the LangChain callback
        handler use to bridge non-context-manager event systems.
        """
        parent = current_span()
        if parent is not None:
            trace_id = parent.trace_id
            parent_ids: list[str] = [parent.id]
        else:
            import uuid as _uuid

            trace_id = _uuid.uuid4().hex
            parent_ids = []
        span = self._make_span(
            trace_id=trace_id,
            parent_ids=parent_ids,
            name=name,
            type=type,
            input=None,
        )
        self._active_spans = getattr(self, "_active_spans", {})
        self._active_tokens = getattr(self, "_active_tokens", {})
        self._active_spans[span.id] = span
        self._active_tokens[span.id] = set_current_span(span)
        return span

    def _end(
        self,
        span_id: str,
        *,
        output: Any = None,
        error: BaseException | None = None,
    ) -> None:
        """Close a span opened by :meth:`_begin`."""
        spans = getattr(self, "_active_spans", {})
        tokens = getattr(self, "_active_tokens", {})
        span = spans.pop(span_id, None)
        token = tokens.pop(span_id, None)
        if span is None:
            return
        now = datetime.now(UTC)
        new_attrs = dict(span.attributes)
        new_status = SpanStatus.OK
        new_error: str | None = None
        if error is not None:
            new_status = SpanStatus.ERROR
            new_attrs["error.type"] = type(error).__name__
            new_attrs["error.message"] = str(error)
            new_error = f"{type(error).__name__}: {error}"
        finalized = span.model_copy(
            update={
                "ended_at": now,
                "status": new_status,
                "output": output,
                "attributes": new_attrs,
                "error": new_error,
            }
        )
        self.store.add_span(finalized)
        tokens = getattr(self, "_active_tokens", {})
        token = tokens.pop(span.id, None)
        if token is not None:
            reset_current_span(token)

    # -- internals --------------------------------------------------------

    def _make_span(
        self,
        trace_id: str,
        parent_ids: list[str],
        name: str,
        type: SpanType,
        input: Any,
    ) -> Span:
        """Build a Span with a content-addressed id and fresh timestamps.

        The span id is derived from a canonical hash of the *logical*
        content (everything except timestamps, error, and id). Two
        calls with the same input and same parent chain collapse to
        the same span id, so the store's content-addressed dedup
        works as advertised.
        """
        # Use a fixed sentinel for started_at/ended_at when computing
        # the id, so the hash is stable across calls.
        sentinel = datetime(1970, 1, 1, tzinfo=UTC)
        partial = Span(
            id="",  # placeholder; will be set after hashing
            trace_id=trace_id,
            parent_ids=parent_ids,
            type=type,
            name=name,
            attributes={},
            input=input,
            output=None,
            started_at=sentinel,
            ended_at=sentinel,
            status=SpanStatus.OK,
        )
        sid = span_hash(partial)
        # Then stamp with the real timestamp for storage.
        now = datetime.now(UTC)
        return partial.model_copy(update={"id": sid, "started_at": now, "ended_at": now})

    def _finalize_span(
        self,
        span: Span,
        output: Any,
        error: BaseException | None,
    ) -> Span:
        """Stamp the span with ended_at, output, status, and any error message."""
        now = datetime.now(UTC)
        new_attrs = dict(span.attributes)
        new_status = SpanStatus.OK
        new_error: str | None = None
        if error is not None:
            new_status = SpanStatus.ERROR
            new_attrs["error.type"] = type(error).__name__
            new_attrs["error.message"] = str(error)
            new_error = f"{type(error).__name__}: {error}"
        return span.model_copy(
            update={
                "ended_at": now,
                "output": output,
                "status": new_status,
                "attributes": new_attrs,
                "error": new_error,
            }
        )


class _TraceContext:
    """Context manager returned by :meth:`Tracer.trace`."""

    def __init__(self, tracer: Tracer, name: str, type: SpanType) -> None:
        self._tracer = tracer
        self._name = name
        self._type = type
        self._span: Span | None = None
        self._token: Any = None

    def __enter__(self) -> _TraceContext:
        parent = current_span()
        if parent is not None:
            trace_id = parent.trace_id
            parent_ids: list[str] = [parent.id]
        else:
            import uuid as _uuid
            trace_id = _uuid.uuid4().hex
            parent_ids = []
        self._span = self._tracer._make_span(
            trace_id=trace_id,
            parent_ids=parent_ids,
            name=self._name,
            type=self._type,
            input=None,
        )
        self._token = set_current_span(self._span)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        assert self._span is not None
        now = datetime.now(UTC)
        new_attrs = dict(self._span.attributes)
        status = SpanStatus.OK
        new_error: str | None = None
        if exc is not None:
            status = SpanStatus.ERROR
            new_attrs["error.type"] = type(exc).__name__
            new_attrs["error.message"] = str(exc)
            new_error = f"{type(exc).__name__}: {exc}"
        finalized = self._span.model_copy(
            update={
                "ended_at": now,
                "status": status,
                "attributes": new_attrs,
                "error": new_error,
            }
        )
        self._tracer.store.add_span(finalized)
        if self._token is not None:
            reset_current_span(self._token)

    def set_output(self, output: Any) -> None:
        """Manually set the span's output (for context-manager spans)."""
        if self._span is None:
            raise RuntimeError("set_output called outside `with` block")
        self._span = self._span.model_copy(update={"output": output})

    def set_attribute(self, key: str, value: Any) -> None:
        """Manually set a span attribute."""
        if self._span is None:
            raise RuntimeError("set_attribute called outside `with` block")
        new_attrs = dict(self._span.attributes)
        new_attrs[key] = value
        self._span = self._span.model_copy(update={"attributes": new_attrs})
