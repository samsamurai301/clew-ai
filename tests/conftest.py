"""Shared pytest fixtures and helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from clew.core.models import Span, SpanStatus, SpanType


def make_span(
    name: str = "step",
    trace_id: str | None = None,
    parent_ids: list[str] | None = None,
    input: object = "in",
    output: object = "out",
    type: SpanType = SpanType.OBSERVATION,
    status: SpanStatus = SpanStatus.OK,
) -> Span:
    """Construct a Span with deterministic, distinct content per call.

    Used by tests that need many distinct spans. The id is randomized
    by default so dedup doesn't accidentally collapse them.
    """
    if trace_id is None:
        trace_id = uuid4().hex
    if parent_ids is None:
        parent_ids = []
    now = datetime.now(UTC)
    # Default id: 64-char hex (will be overwritten by store if needed).
    return Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=parent_ids,
        type=type,
        name=name,
        attributes={"test": True, "step": name},
        input=input,
        output=output,
        started_at=now,
        ended_at=now,
        status=status,
    )
