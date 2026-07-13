"""Structural diff of two clew traces.

A :class:`TraceDiff` compares two traces span-by-span. Spans are
matched by their *path from the root* — the concatenation of
``span.name`` along the parent chain. This is stable across the
two traces as long as the structure is similar, even if individual
span ids differ (which they will, because content-addressed spans
get fresh ids on replay).

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

from dataclasses import dataclass, field
from typing import Any

from clew.core.models import Span, Trace

# Sentinel "path separator" used when joining span names. Unlikely
# to appear in real span names because OTel semantic conventions
# discourage punctuation in operation names.
_PATH_SEP: str = "\x1f"


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


def _path_of(span: Span, by_id: dict[str, Span]) -> str:
    """Return the canonical "path from root" of a span.

    The path is the tuple of names along the parent chain, root
    first, joined by ``_PATH_SEP``. Spans with multiple parents
    (DAG, not tree) get a deterministic path by following parents
    in sorted order.
    """
    chain: list[str] = []
    seen: set[str] = set()
    cursor: str | None = span.id
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        node = by_id.get(cursor)
        if node is None:
            break
        chain.append(node.name)
        parents = sorted(node.parent_ids)
        cursor = parents[0] if parents else None
    chain.reverse()
    return _PATH_SEP.join(chain)


def _index(trace: Trace) -> tuple[dict[str, Span], dict[str, str]]:
    """Return ``(by_id, path_by_id)`` for a trace."""
    by_id = {s.id: s for s in trace.spans}
    path_by_id = {sid: _path_of(span, by_id) for sid, span in by_id.items()}
    return by_id, path_by_id


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
    _, paths_a = _index(trace_a)
    _, paths_b = _index(trace_b)
    spans_by_path_a: dict[str, Span] = {p: trace_a.spans[0] for p in paths_a.values()}  # placeholder
    # Build proper maps.
    spans_by_path_a = {paths_a[s.id]: s for s in trace_a.spans}
    spans_by_path_b = {paths_b[s.id]: s for s in trace_b.spans}
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
        lines.append(f"~ {a.name} (input={a.input!r}, output={a.input!r} -> {b.output!r})")
    for span in d.added:
        lines.append(f"+ {span.name}: {span.output!r}")
    for span in d.removed:
        lines.append(f"- {span.name}: {span.output!r}")
    return "\n".join(lines)


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
            "modified": [
                {"before": _span_dict(a), "after": _span_dict(b)} for a, b in d.modified
            ],
            "unchanged_count": d.unchanged_count,
        },
        indent=2,
        default=str,
    )
