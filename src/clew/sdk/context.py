"""Span context: task-local storage for the currently-active span.

A *span context* is the runtime notion of "the span we are currently
executing." It is stored in a :class:`contextvars.ContextVar` so that
concurrent tasks (anyio, asyncio) each have their own context —
nesting is automatic, and parallel branches do not contaminate
each other.

This is the layer the :class:`Tracer` uses to know the parent of
a new span: a ``@tracer.span`` decorator reads the current span,
makes the new span a child of it, and sets the new span as current
for the duration of the wrapped function.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

# A single ContextVar that holds the *currently active* span, or None.
# Using one global ContextVar (rather than per-thread) means anyio
# task context automatically propagates the value to child tasks,
# but parallel tasks start with the parent task's value (which is
# the desired behavior for nested tracing).
_current_span: ContextVar[Any] = ContextVar("clew_current_span", default=None)


def current_span() -> Any:
    """Return the span currently in scope, or ``None`` if outside any span."""
    return _current_span.get()


def set_current_span(span: Any) -> Token[Any]:
    """Set the active span. Returns a token that can be passed to :func:`reset_current_span`."""
    return _current_span.set(span)


def reset_current_span(token: Token[Any]) -> None:
    """Restore the previous active span (passed the token from :func:`set_current_span`)."""
    _current_span.reset(token)


def current_trace_id() -> str | None:
    """Convenience: return the trace id of the active span, or None."""
    span = _current_span.get()
    if span is None:
        return None
    return getattr(span, "trace_id", None)
