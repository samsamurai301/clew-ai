"""Tests for NDJSON bulk transport (clew.core.format)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from clew.core.format import (
    export_ndjson,
    import_ndjson,
    read_ndjson,
    to_otel,
    write_ndjson,
)
from clew.core.models import Span, SpanStatus, SpanType


def _make_span(
    trace_id: str,
    *,
    parent_ids: list[str] | None = None,
    name: str = "root",
    type: SpanType = SpanType.OBSERVATION,
    status: SpanStatus = SpanStatus.OK,
    error: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Span:
    return Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=parent_ids or [],
        type=type,
        name=name,
        attributes={"k": "v"},
        input="x",
        output="y",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 2, tzinfo=UTC),
        status=status,
        error=error,
        metadata=metadata,
    )


def test_export_ndjson_header_and_spans() -> None:
    """export_ndjson writes a header line followed by one span per line."""
    tid = uuid4().hex
    spans = [_make_span(tid, name="a"), _make_span(tid, name="b")]
    out = export_ndjson(tid, spans)
    lines = out.strip().split("\n")
    assert len(lines) == 3
    header = json.loads(lines[0])
    assert header == {"_kind": "trace", "trace_id": tid, "span_count": 2}
    for ln in lines[1:]:
        obj = json.loads(ln)
        assert obj["_kind"] == "span"
        assert obj["trace_id"] == tid


def test_import_ndjson_round_trip() -> None:
    """A trace survives an export -> import cycle."""
    tid = uuid4().hex
    spans = [
        _make_span(tid, name="root"),
        _make_span(tid, parent_ids=["<will-fix>"], name="child", type=SpanType.LLM),
    ]
    out = export_ndjson(tid, spans)
    got_tid, got = import_ndjson(out)
    assert got_tid == tid
    assert len(got) == 2
    assert got[0].name == "root"
    assert got[1].name == "child"
    assert got[1].type == SpanType.LLM


def test_import_ndjson_handles_bare_otel_form() -> None:
    """import_ndjson accepts bare OTel spans (no clew header)."""
    tid = uuid4().hex
    span = _make_span(tid, name="root")
    bare = json.dumps(to_otel(span))
    got_tid, got = import_ndjson(bare + "\n")
    assert got_tid == tid
    assert len(got) == 1
    assert got[0].name == "root"


def test_import_ndjson_rejects_empty() -> None:
    with pytest.raises(ValueError, match="no spans"):
        import_ndjson("")


def test_import_ndjson_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        import_ndjson("{not json")


def test_import_ndjson_rejects_non_object_line() -> None:
    with pytest.raises(ValueError, match="not a JSON object"):
        import_ndjson("[1, 2, 3]")


def test_import_ndjson_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown _kind"):
        import_ndjson(json.dumps({"_kind": "banana"}))


def test_import_ndjson_rejects_trace_header_without_id() -> None:
    with pytest.raises(ValueError, match="trace_id"):
        import_ndjson(json.dumps({"_kind": "trace", "span_count": 0}))


def test_write_and_read_ndjson_round_trip(tmp_path: Path) -> None:
    """write_ndjson + read_ndjson round-trip via the filesystem."""
    tid = uuid4().hex
    spans = [
        _make_span(tid, name="root"),
        _make_span(tid, parent_ids=["x"], name="child", status=SpanStatus.ERROR, error="boom"),
    ]
    out = tmp_path / "trace.ndjson"
    n = write_ndjson(out, tid, spans)
    assert n == 2
    got_tid, got = read_ndjson(out)
    assert got_tid == tid
    assert [s.name for s in got] == ["root", "child"]
    assert got[1].status == SpanStatus.ERROR
    assert got[1].error == "boom"


def test_export_ndjson_empty_trace_emits_just_header() -> None:
    """An empty trace still writes a valid header line."""
    tid = uuid4().hex
    out = export_ndjson(tid, [])
    parsed = json.loads(out.strip())
    assert parsed == {"_kind": "trace", "trace_id": tid, "span_count": 0}


def test_export_preserves_metadata() -> None:
    """Metadata round-trips through NDJSON."""
    tid = uuid4().hex
    s = _make_span(tid, metadata={"model": "gpt-4o", "n": 3})
    out = export_ndjson(tid, [s])
    _got_tid, got = import_ndjson(out)
    assert got[0].metadata == {"model": "gpt-4o", "n": 3}


def test_export_preserves_parent_chain() -> None:
    """Parent ids round-trip through NDJSON."""
    tid = uuid4().hex
    root = _make_span(tid, name="root")
    child = _make_span(tid, name="child", parent_ids=[root.id])
    out = export_ndjson(tid, [root, child])
    _, got = import_ndjson(out)
    by_name = {s.name: s for s in got}
    assert by_name["child"].parent_ids == [root.id]
