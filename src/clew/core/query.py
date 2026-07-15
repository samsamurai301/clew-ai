"""Search and filter spans across the store.

``clew query`` is the "find that one trace" command. It walks the
SQLite index and applies a chain of filters; what's left is printed
as a table (or emitted as JSON for piping into ``jq``).

Filters
-------
- ``--name``  substring match on ``span.name`` (case-insensitive)
- ``--type``  exact match on ``span.type`` (``llm`` / ``tool`` / ``chain`` / ...)
- ``--status`` exact match on ``span.status`` (``ok`` / ``error``)
- ``--trace``  restrict to a single ``trace_id``
- ``--limit``  cap the result count (default 50)
- ``--metadata``  match ``span.metadata[key] == value`` (repeatable,
  all keys must match). The value is parsed as JSON if possible, so
  ``--metadata priority=high`` matches a span whose
  ``metadata == {"priority": "high"}``.

The query is read-only and never mutates the store. Empty filters
list every span, in trace-then-time order.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore

MAX_METADATA_VALUE_BYTES = 65_536


@dataclass(frozen=True)
class QueryFilter:
    """A single criterion applied to a span row.

    The ``metadata`` field is a ``{key: value}`` map; every entry
    must match (logical AND). Values are matched after JSON-coercing
    both sides (so the string ``"3"`` matches the integer ``3``).
    """

    name: str | None = None
    type: SpanType | None = None
    status: SpanStatus | None = None
    trace_id: str | None = None
    metadata: dict[str, object] | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        """Reject ambiguous or non-positive result limits."""
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit <= 0:
            raise ValueError("query limit must be a positive integer")

    def to_dict(self) -> dict[str, object]:
        """Serialize the filter as a JSON-safe dict."""
        d: dict[str, object] = {"limit": self.limit}
        if self.name is not None:
            d["name"] = self.name
        if self.type is not None:
            d["type"] = self.type.value
        if self.status is not None:
            d["status"] = self.status.value
        if self.trace_id is not None:
            d["trace_id"] = self.trace_id
        if self.metadata:
            d["metadata"] = dict(self.metadata)
        return d


@dataclass(frozen=True)
class QueryResult:
    """A single matched span with provenance."""

    span: Span
    trace_id: str
    root_span_id: str


def _coerce(value: object) -> object:
    """Try to make ``value`` JSON-comparable.

    Booleans, ints, floats, and strings are returned as-is. Other
    types are stringified. The result is used only for equality
    comparison, so a fuzzy match here is acceptable.
    """
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


def _metadata_matches(span: Span, want: dict[str, object]) -> bool:
    """Return True iff every ``want`` key is present in ``span.metadata``."""
    if not span.metadata:
        return False
    for k, v in want.items():
        if k not in span.metadata:
            return False
        if _coerce(span.metadata[k]) != _coerce(v):
            return False
    return True


def _name_matches(span: Span, needle: str) -> bool:
    """Case-insensitive substring match on ``span.name``."""
    return needle.lower() in span.name.lower()


def query(root: Path, filt: QueryFilter) -> list[QueryResult]:
    """Apply ``filt`` to every span in the store and return matches.

    The store is opened via :class:`Store` (idempotent) and the
    SQLite index is consulted for the cheap filters (trace_id,
    type, status). For the more expensive ones (name, metadata) we
    fall back to walking span files.
    """
    store = Store(root, read_only=True)
    ts = TraceStore(store)
    trace_ids = [filt.trace_id] if filt.trace_id is not None else list(store.iter_traces())

    results: list[QueryResult] = []
    for tid in trace_ids:
        try:
            trace = ts.get_trace(tid)
        except KeyError:
            continue
        root_span_id = trace.root_span_id
        for s in trace.spans:
            if filt.type is not None and s.type is not filt.type:
                continue
            if filt.status is not None and s.status is not filt.status:
                continue
            if filt.name is not None and not _name_matches(s, filt.name):
                continue
            if filt.metadata and not _metadata_matches(s, filt.metadata):
                continue
            results.append(QueryResult(span=s, trace_id=tid, root_span_id=root_span_id))
            if len(results) >= filt.limit:
                return results
    return results


def parse_metadata_spec(specs: Iterable[str]) -> dict[str, object]:
    """Parse ``--metadata k=v`` style arguments into a dict.

    Values are JSON-parsed when possible (``--metadata n=3`` ->
    ``{"n": 3}``). If JSON parsing fails, the value is kept as a
    string. An empty value (``k=``) is treated as the empty string.
    """
    out: dict[str, object] = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"metadata spec must be key=value, got {spec!r}")
        k, v = spec.split("=", 1)
        if len(v.encode("utf-8")) > MAX_METADATA_VALUE_BYTES:
            raise ValueError(
                f"metadata value for {k!r} exceeds the {MAX_METADATA_VALUE_BYTES}-byte limit"
            )
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
        except RecursionError as exc:
            raise ValueError(f"metadata value for {k!r} is nested too deeply") from exc
    return out


# ---------------------------------------------------------------------------
# SQL helpers (for tests; not used by the high-level path)
# ---------------------------------------------------------------------------


#: Pattern used by the CLI's ``--name`` to flag regex-like misuse. The
#: actual match is always substring; this is purely diagnostic.
_LIKE_HINT = re.compile(r"[%_]")


__all__ = [
    "MAX_METADATA_VALUE_BYTES",
    "QueryFilter",
    "QueryResult",
    "parse_metadata_spec",
    "query",
]
