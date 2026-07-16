"""Shared pytest fixtures and helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from clew.core.models import Span, SpanStatus, SpanType

_SEQUENCES: defaultdict[str, int] = defaultdict(int)


def make_span(
    name: str = "step",
    trace_id: str | None = None,
    parent_ids: list[str] | None = None,
    input: object = "in",
    output: object = "out",
    type: SpanType = SpanType.OBSERVATION,
    status: SpanStatus = SpanStatus.OK,
    sequence: int | None = None,
) -> Span:
    """Construct a Span with deterministic, distinct content per call.

    Used by tests that need many distinct spans. The id is randomized
    by default so dedup doesn't accidentally collapse them.
    """
    if trace_id is None:
        trace_id = uuid4().hex
    elif len(trace_id) != 32:
        trace_id = uuid5(NAMESPACE_URL, f"clew-test-trace:{trace_id}").hex
    if parent_ids is None:
        parent_ids = []
    now = datetime.now(UTC)
    if sequence is None:
        sequence = _SEQUENCES[trace_id]
        _SEQUENCES[trace_id] += 1
    return Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=parent_ids,
        sequence=sequence,
        type=type,
        name=name,
        attributes={"test": True, "step": name},
        input=input,
        output=output,
        started_at=now,
        ended_at=now,
        status=status,
        error="test error" if status is SpanStatus.ERROR else None,
    )
