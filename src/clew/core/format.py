"""OpenTelemetry-compatible projection of :class:`clew.core.models.Span`.

The OTel GenAI semantic conventions are the lingua franca of agent
observability, and clew honors them so that OTel-instrumented code
"just works" with clew's branching and replay. This module is the
mapping layer: :func:`to_otel` renders a clew span as a dict an OTel
consumer can read; :func:`from_otel` parses an OTel-style span dict
back into a clew :class:`Span`.

The mapping is deliberately liberal on read (``from_otel``) and
explicit on write (``to_otel``): we accept several common OTel span
shapes and produce a canonical clew dict on the way out.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clew.core.models import Span, SpanStatus, SpanType

#: A frozenset for fast membership tests on the four SpanType values.
_SPAN_TYPES: frozenset[str] = frozenset(t.value for t in SpanType)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_time(value: datetime) -> str:
    """Format a datetime as RFC 3339 UTC with the ``Z`` suffix.

    Matches the OTel / GenAI semantic convention: fractional seconds
    are emitted with millisecond (3-digit) precision. Python's
    :py:meth:`datetime.isoformat` always produces 6-digit microseconds
    (e.g. ``.123000`` for 123 ms); we truncate the trailing three
    zeros so the round-trip with OTel consumers stays byte-stable.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    s = value.astimezone(UTC).isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    # Truncate microseconds → milliseconds (keep ``.NNN``).
    dot_index = s.find(".")
    if dot_index >= 0:
        s = s[: dot_index + 4] + s[dot_index + 7 :]
    return s


def _parse_time(value: Any) -> datetime:
    """Parse an OTel timestamp into a timezone-aware UTC :class:`datetime`.

    Accepts a :class:`datetime` (returned with UTC tzinfo if naive),
    a string in ISO 8601 form (with optional ``Z`` suffix), or a
    numeric epoch in seconds. Raises :class:`ValueError` on garbage.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if not isinstance(value, str):
        raise ValueError(
            f"Cannot parse timestamp from value of type {type(value).__name__!r}"
        )
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # ``fromisoformat`` accepts most ISO 8601 forms including the
    # ``+HH:MM`` suffix we just produced.
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Some OTel producers omit the timezone entirely; assume UTC.
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
        raise
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _infer_type(attrs: Mapping[str, Any], explicit: str | None = None) -> SpanType:
    """Pick a :class:`SpanType` from explicit text or attribute heuristics.

    Tool attributes win because ``gen_ai.tool.*`` is unambiguous;
    LLM-related GenAI keys come next; everything else is an
    :attr:`SpanType.OBSERVATION` (a safe default for things we don't
    recognize).
    """
    if explicit:
        try:
            return SpanType(explicit)
        except ValueError:
            pass
    if "gen_ai.tool.name" in attrs or "gen_ai.tool.call.id" in attrs:
        return SpanType.TOOL
    if any(
        key in attrs
        for key in (
            "gen_ai.system",
            "gen_ai.request.model",
            "gen_ai.usage.input_tokens",
            "gen_ai.completion",
        )
    ):
        return SpanType.LLM
    return SpanType.OBSERVATION


def _extract_status(payload: Any) -> tuple[SpanStatus, str | None]:
    """Extract ``(status, error)`` from an OTel status value.

    ``payload`` may be a string (code only), a dict with ``code`` and
    optional ``message``, or ``None``. The OTel canonical status codes
    are ``OK``, ``ERROR``, and ``UNSET`` (we treat ``UNSET`` as
    :attr:`SpanStatus.OK`).
    """
    if payload is None:
        return SpanStatus.OK, None
    if isinstance(payload, str):
        code = payload.strip().upper()
        message: str | None = None
    elif isinstance(payload, Mapping):
        code = str(payload.get("code", "OK")).strip().upper()
        raw_msg = payload.get("message")
        message = str(raw_msg) if raw_msg else None
    else:
        return SpanStatus.OK, None
    if code == "ERROR":
        return SpanStatus.ERROR, message or "error"
    if code == "RUNNING":
        return SpanStatus.RUNNING, None
    # "OK", "UNSET", or anything unrecognized defaults to OK.
    return SpanStatus.OK, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_otel(span: Span) -> dict[str, Any]:
    """Convert a clew :class:`Span` to an OTel-style dict.

    The output is a plain ``dict`` (not a protobuf or an OTel SDK
    object) and is round-trippable through :func:`from_otel`. All
    ``gen_ai.*`` attributes live under the ``attributes`` key; clew
    top-level fields are mapped to their OTel conventions (``name``,
    ``kind``, ``start_time``, ``end_time``, ``status``).
    """
    attributes = dict(span.attributes)
    result: dict[str, Any] = {
        "name": span.name,
        "kind": span.type.value,
        "start_time": _normalize_time(span.started_at),
        "end_time": _normalize_time(span.ended_at),
        "status": {"code": span.status.value, "message": span.error or ""},
        "attributes": attributes,
        "trace_id": span.trace_id,
        "span_id": span.id,
        "parent_span_id": span.parent_ids[0] if span.parent_ids else None,
        "input": span.input,
        "output": span.output,
    }
    if span.metadata is not None:
        result["metadata"] = dict(span.metadata)
    return result


def from_otel(otel_span: Mapping[str, Any]) -> Span:
    """Parse an OTel-style span dict into a clew :class:`Span`.

    The parser is permissive: it accepts the most common OTel span
    shapes (the OTel SDK's JSON exporter, the Pythonic dict produced
    by manual instrumentation, and a simplified flat form). The
    required keys are ``name`` (or ``gen_ai.operation.name``) and a
    start time (``start_time`` or ``started_at``). Everything else has
    a sensible default.
    """
    if not isinstance(otel_span, Mapping):
        raise TypeError(
            f"from_otel expected a mapping, got {type(otel_span).__name__!r}"
        )
    attributes: dict[str, Any] = dict(otel_span.get("attributes") or {})

    # Name: prefer the OTel canonical "name" key, then gen_ai.operation.name.
    raw_name = otel_span.get("name") or attributes.get("gen_ai.operation.name")
    name = str(raw_name) if raw_name else "span"

    # Type: explicit "kind"/"type" if valid, else infer from attributes.
    kind = otel_span.get("kind") or otel_span.get("type")
    if kind in _SPAN_TYPES:
        span_type = SpanType(kind)
    else:
        span_type = _infer_type(attributes, kind if isinstance(kind, str) else None)

    # Timing: required; we surface a clear error if it's missing.
    start_value = otel_span.get("start_time")
    if start_value is None:
        start_value = otel_span.get("started_at")
    if start_value is None:
        raise ValueError(
            "OTel span must have a 'start_time' (or 'started_at') field"
        )
    started_at = _parse_time(start_value)
    end_value = otel_span.get("end_time")
    if end_value is None:
        end_value = otel_span.get("ended_at")
    ended_at = _parse_time(end_value) if end_value is not None else started_at

    status, error = _extract_status(otel_span.get("status"))

    # Identity: OTel's span/trace ids are typically 16-byte hex; clew
    # uses 32-byte hex. We store whatever the caller gave us; the
    # hash derivation in core/models.Span uses the canonical form.
    trace_id = str(otel_span.get("trace_id") or "")
    span_id = str(otel_span.get("span_id") or otel_span.get("id") or "")

    parent_id = otel_span.get("parent_span_id")
    if parent_id is None:
        parent_id = otel_span.get("parent_id")
    if parent_id is None:
        context = otel_span.get("context")
        if isinstance(context, Mapping):
            parent_id = context.get("parent_span_id")
    parent_ids: list[str] = [str(parent_id)] if parent_id else []

    return Span(
        id=span_id,
        trace_id=trace_id,
        parent_ids=parent_ids,
        type=span_type,
        name=name,
        attributes=attributes,
        input=otel_span.get("input"),
        output=otel_span.get("output"),
        started_at=started_at,
        ended_at=ended_at,
        status=status,
        error=error,
        metadata=otel_span.get("metadata"),
    )


# ---------------------------------------------------------------------------
# NDJSON bulk transport
# ---------------------------------------------------------------------------


def export_ndjson(trace_id: str, spans: Iterable[Span]) -> str:
    """Render a trace as one JSON object per line (NDJSON).

    The output is a single string suitable for writing to a file or
    piping into ``jq``. The format is:

        {"_kind": "trace", "trace_id": "...", "span_count": 3}
        {"_kind": "span", ...otel...}
        {"_kind": "span", ...otel...}

    The leading ``_kind: trace`` header is a clew extension: it lets
    :func:`import_ndjson` recover the trace id even when the consumer
    has already sharded spans by trace. Plain OTel consumers can
    ignore the header (it lives at the dict level, not in attributes).
    """
    span_list = list(spans)
    header: dict[str, Any] = {
        "_kind": "trace",
        "trace_id": trace_id,
        "span_count": len(span_list),
    }
    lines = [json.dumps(header, sort_keys=True)]
    for s in span_list:
        d = to_otel(s)
        d["_kind"] = "span"
        lines.append(json.dumps(d, sort_keys=True))
    return "\n".join(lines) + "\n"


#: Default cap on NDJSON input size (defense against zip/json bombs).
#: 64MB is generous for a single trace; if you need more, pass
#: ``max_bytes`` to :func:`import_ndjson`.
DEFAULT_MAX_NDJSON_BYTES: int = 64 * 1024 * 1024


def import_ndjson(
    text: str,
    *,
    max_bytes: int = DEFAULT_MAX_NDJSON_BYTES,
    max_spans: int = 1_000_000,
) -> tuple[str, list[Span]]:
    """Parse an NDJSON trace file back into ``(trace_id, [Span])``.

    Accepts both clew's wrapped form (with a leading ``_kind: trace``
    header) and the bare OTel form (one OTel span dict per line, all
    sharing a ``trace_id``). Raises :class:`ValueError` on malformed
    input or if the input exceeds ``max_bytes`` / ``max_spans``.
    """
    if len(text.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"NDJSON input exceeds {max_bytes} bytes (use max_bytes to override)"
        )
    trace_id: str | None = None
    spans: list[Span] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {n} is not valid JSON: {exc}") from exc
        if not isinstance(obj, Mapping):
            raise ValueError(f"line {n} is not a JSON object")
        kind = obj.get("_kind")
        if kind == "trace":
            tid = obj.get("trace_id")
            if not isinstance(tid, str):
                raise ValueError(f"line {n}: trace header is missing trace_id")
            trace_id = tid
            continue
        if kind is not None and kind != "span":
            raise ValueError(f"line {n}: unknown _kind {kind!r}")
        if len(spans) >= max_spans:
            raise ValueError(
                f"NDJSON input has more than {max_spans} spans (use max_spans to override)"
            )
        span = from_otel(obj)
        spans.append(span)
        if trace_id is None:
            # Bare OTel form: take the first span's trace_id.
            trace_id = span.trace_id
    if trace_id is None:
        raise ValueError("no spans found in NDJSON input")
    return trace_id, spans


def write_ndjson(path: Path, trace_id: str, spans: Iterable[Span]) -> int:
    """Write spans to ``path`` as NDJSON; returns the number written."""
    text = export_ndjson(trace_id, spans)
    path.write_text(text, encoding="utf-8")
    return text.count("\n") - 1  # minus the header line


def read_ndjson(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_NDJSON_BYTES,
    max_spans: int = 1_000_000,
) -> tuple[str, list[Span]]:
    """Read spans from an NDJSON file; returns ``(trace_id, [Span])``.

    Enforces the same ``max_bytes`` / ``max_spans`` caps as
    :func:`import_ndjson` to refuse zip/json bombs.
    """
    if path.stat().st_size > max_bytes:
        raise ValueError(
            f"file {path} exceeds {max_bytes} bytes (use max_bytes to override)"
        )
    return import_ndjson(path.read_text(encoding="utf-8"), max_bytes=max_bytes, max_spans=max_spans)


__all__ = [
    "DEFAULT_MAX_NDJSON_BYTES",
    "export_ndjson",
    "from_otel",
    "import_ndjson",
    "read_ndjson",
    "to_otel",
    "write_ndjson",
]
