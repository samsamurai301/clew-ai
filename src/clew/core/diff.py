"""Structural diff of two Clew traces.

Occurrences are matched by their full ancestry, type/name, and occurrence
order among equivalent siblings. Repeated sibling names therefore remain
distinct instead of overwriting one another in a dictionary.

Three match outcomes are reported:

* **added** — the path exists in B but not in A.
* **removed** — the path exists in A but not in B.
* **modified** — the path exists in both, but the spans differ.
  Two spans are considered "the same" by :func:`diff` if they share
  a path; they are "modified" if their content hashes differ.
* **unchanged** — counted, not listed (a trace with N spans and M
  modifications has N - M - added_count - removed_count unchanged
  spans, after accounting for the added/removed in the diff).
"""

from __future__ import annotations

import heapq
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from clew.core.models import Span, Trace

StructuralKey = int
StructuralSignature = tuple[tuple[StructuralKey, ...], str, str, int]


@dataclass
class TraceDiff:
    """The result of :func:`diff` — what changed between two traces.

    Attributes
    ----------
    trace_id_a, trace_id_b
        The id of the two traces being compared.
    added
        Spans in B whose path was not in A.
    removed
        Spans in A whose path was not in B.
    modified
        Pairs ``(span_from_a, span_from_b)`` where the path matched
        but the content differs.
    unchanged_count
        How many spans were identical (by content hash) in both
        traces. Spans that share a path and have the same hash.
    """

    trace_id_a: str
    trace_id_b: str
    added: list[Span] = field(default_factory=list)
    removed: list[Span] = field(default_factory=list)
    modified: list[tuple[Span, Span]] = field(default_factory=list)
    unchanged_count: int = 0


def _index(
    trace: Trace,
    interner: dict[StructuralSignature, StructuralKey],
) -> dict[StructuralKey, Span]:
    """Build a collision-free structural index in linearithmic time.

    A shared interner gives equivalent structural occurrences in both traces
    the same compact integer key. Parent ancestry is represented by already
    interned parent keys, avoiding recursively nested tuples and repeated
    hashing proportional to trace depth.
    """
    by_id = {span.id: span for span in trace.spans}
    in_degree = {span.id: len(span.parent_ids) for span in trace.spans}
    children: dict[str, list[str]] = {span.id: [] for span in trace.spans}
    missing: set[str] = set()
    for span in trace.spans:
        for parent in span.parent_ids:
            if parent not in by_id:
                missing.add(parent)
            else:
                children[parent].append(span.id)
    if missing:
        raise ValueError(
            f"Cannot diff malformed trace topology; unresolved parents: {sorted(missing)}"
        )

    key_by_id: dict[str, StructuralKey] = {}
    occurrence: dict[tuple[tuple[StructuralKey, ...], str, str], int] = {}
    ready = [
        (by_id[span_id].sequence, span_id) for span_id, degree in in_degree.items() if degree == 0
    ]
    heapq.heapify(ready)
    while ready:
        _, span_id = heapq.heappop(ready)
        span = by_id[span_id]
        parent_keys = tuple(key_by_id[parent] for parent in span.parent_ids)
        sibling_group = (parent_keys, span.type.value, span.name)
        ordinal = occurrence.get(sibling_group, 0)
        occurrence[sibling_group] = ordinal + 1
        signature = (*sibling_group, ordinal)
        key = interner.setdefault(signature, len(interner))
        key_by_id[span.id] = key
        for child_id in children[span_id]:
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                child = by_id[child_id]
                heapq.heappush(ready, (child.sequence, child.id))

    if len(key_by_id) != len(trace.spans):
        unresolved = sorted(set(by_id) - set(key_by_id))
        raise ValueError(
            f"Cannot diff malformed trace topology; cycle involves spans: {unresolved}"
        )
    return {key_by_id[span.id]: span for span in trace.spans}


def diff(trace_a: Trace, trace_b: Trace) -> TraceDiff:
    """Compute the structural diff between two traces.

    Spans are matched by their path from the root. Matching spans
    that differ in content hash are "modified"; matching spans
    with the same hash are "unchanged" (counted, not listed).
    Spans in B without a path match in A are "added"; the
    symmetric case is "removed".

    The result is deterministic: ``added``, ``removed``, and
    ``modified`` are each sorted by the path string, so two calls
    with the same inputs produce the same output.
    """
    interner: dict[StructuralSignature, StructuralKey] = {}
    spans_by_path_a = _index(trace_a, interner)
    spans_by_path_b = _index(trace_b, interner)
    added: list[Span] = []
    removed: list[Span] = []
    modified: list[tuple[Span, Span]] = []
    unchanged_count = 0
    all_paths = set(spans_by_path_a) | set(spans_by_path_b)
    for path in sorted(all_paths):
        in_a = path in spans_by_path_a
        in_b = path in spans_by_path_b
        if in_a and not in_b:
            removed.append(spans_by_path_a[path])
        elif in_b and not in_a:
            added.append(spans_by_path_b[path])
        else:
            sa = spans_by_path_a[path]
            sb = spans_by_path_b[path]
            if _content_hash(sa) == _content_hash(sb):
                unchanged_count += 1
            else:
                modified.append((sa, sb))
    return TraceDiff(
        trace_id_a=trace_a.trace_id,
        trace_id_b=trace_b.trace_id,
        added=added,
        removed=removed,
        modified=modified,
        unchanged_count=unchanged_count,
    )


def _content_hash(span: Span) -> str:
    """Hash a span's content (everything except the id/trace_id/timestamps/parent_ids).

    Two spans with the same content hash are "the same" for diffing
    purposes. We exclude ``started_at``/``ended_at`` and ``parent_ids``
    so that two replays of the same logical step don't show up as
    "modified" just because they ran at different times or because
    their parent ids were freshly minted.
    """
    from clew.utils.hash import content_hash as _ch

    payload: dict[str, Any] = {
        "type": span.type,
        "name": span.name,
        "attributes": span.attributes,
        "input": span.input,
        "output": span.output,
        "status": span.status,
        "error": span.error,
        "metadata": span.metadata,
    }
    return _ch(payload)


def format_text(d: TraceDiff) -> str:
    """Format a :class:`TraceDiff` as a human-readable string.

    Uses ANSI color codes when the terminal supports it (the
    optional ``colorama``-style logic is kept simple — we always
    emit codes, and the user's terminal can disable them).
    """
    lines: list[str] = []
    lines.append(f"--- trace {d.trace_id_a}")
    lines.append(f"+++ trace {d.trace_id_b}")
    lines.append(
        f"@@ {len(d.modified)} modified, +{len(d.added)} -{len(d.removed)}, {d.unchanged_count} unchanged @@"
    )
    for a, b in d.modified:
        lines.append(
            f"~ {_terminal_safe(a.name)} "
            f"(input={_terminal_safe(repr(a.input))}, "
            f"output={_terminal_safe(repr(a.output))} -> "
            f"{_terminal_safe(repr(b.output))})"
        )
    for span in d.added:
        lines.append(f"+ {_terminal_safe(span.name)}: {_terminal_safe(repr(span.output))}")
    for span in d.removed:
        lines.append(f"- {_terminal_safe(span.name)}: {_terminal_safe(repr(span.output))}")
    return "\n".join(lines)


def _terminal_safe(value: str) -> str:
    """Neutralize invisible terminal controls in hostile trace text."""
    return "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char) in {"Cc", "Cf"} else char
        for char in value
    )


def format_json(d: TraceDiff) -> str:
    """Format a :class:`TraceDiff` as JSON for programmatic use."""
    import json

    def _span_dict(s: Span) -> dict[str, Any]:
        return {
            "id": s.id,
            "name": s.name,
            "type": s.type,
            "input": s.input,
            "output": s.output,
            "status": s.status,
            "attributes": s.attributes,
        }

    return json.dumps(
        {
            "trace_a": d.trace_id_a,
            "trace_b": d.trace_id_b,
            "added": [_span_dict(s) for s in d.added],
            "removed": [_span_dict(s) for s in d.removed],
            "modified": [{"before": _span_dict(a), "after": _span_dict(b)} for a, b in d.modified],
            "unchanged_count": d.unchanged_count,
        },
        indent=2,
        default=str,
    )
