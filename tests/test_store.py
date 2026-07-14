"""Tests for clew.core.store — content-addressed store, dedup, indexing, iteration."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_span(
    *,
    span_id: str,
    trace_id: str = "t" * 64,
    name: str = "test",
    parent_ids: list[str] | None = None,
    span_type: SpanType = SpanType.LLM,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    attributes: dict[str, object] | None = None,
    input_data: object | None = None,
    output_data: object | None = None,
) -> Span:
    """Build a Span with sensible defaults for store tests."""
    return Span(
        id=span_id,
        trace_id=trace_id,
        parent_ids=list(parent_ids or []),
        type=span_type,
        name=name,
        attributes=dict(attributes or {}),
        input=input_data,
        output=output_data,
        started_at=started_at or datetime(2026, 7, 13, 18, 0, 0, tzinfo=UTC),
        ended_at=ended_at or datetime(2026, 7, 13, 18, 0, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """A fresh Store rooted at a tmp_path/.clew/ directory."""
    root = tmp_path / ".clew"
    return Store(root)


# ---------------------------------------------------------------------------
# Manifest + HEAD
# ---------------------------------------------------------------------------


def test_manifest_written_on_init(tmp_path: Path) -> None:
    """Initializing a Store writes a manifest.json with version=1."""
    root = tmp_path / ".clew"
    Store(root)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["version"] == 1
    assert "created_at" in manifest


def test_manifest_not_overwritten_on_reopen(tmp_path: Path) -> None:
    """A second Store on the same root preserves the original manifest."""
    root = tmp_path / ".clew"
    s1 = Store(root)
    original = (root / "manifest.json").read_text()
    # Mutate the manifest to detect a re-write.
    (root / "manifest.json").write_text('{"version": 1, "created_at": "OLD"}')
    Store(root)
    assert (root / "manifest.json").read_text() == '{"version": 1, "created_at": "OLD"}'
    # And the first store's handle is still valid.
    assert s1 is not None


def test_head_defaults_to_main(tmp_path: Path) -> None:
    """The HEAD file exists and points to the main branch by default."""
    root = tmp_path / ".clew"
    Store(root)
    assert (root / "HEAD").read_text().strip() == "main"


def test_directory_layout(tmp_path: Path) -> None:
    """The expected subdirectories are created on init."""
    root = tmp_path / ".clew"
    Store(root)
    assert (root / "spans").is_dir()
    assert (root / "refs").is_dir()
    assert (root / "index.sqlite").is_file()


# ---------------------------------------------------------------------------
# put / get / has
# ---------------------------------------------------------------------------


def test_put_returns_span_id(store: Store) -> None:
    """put returns the span's id."""
    span = _make_span(span_id="a" * 64)
    assert store.put(span) == "a" * 64


def test_get_roundtrip(store: Store) -> None:
    """A span put and then get returns an equal span."""
    span = _make_span(
        span_id="a" * 64,
        name="roundtrip",
        attributes={"gen_ai.system": "openai", "k": [1, 2, 3]},
        input_data={"messages": [{"role": "user", "content": "hi"}]},
        output_data={"text": "hello"},
    )
    store.put(span)
    loaded = store.get(span.id)
    assert loaded == span


def test_get_raises_keyerror_for_missing(store: Store) -> None:
    """get raises KeyError for an unknown span id."""
    with pytest.raises(KeyError):
        store.get("f" * 64)


def test_has_true_for_existing(store: Store) -> None:
    """has returns True for a span that was put."""
    span = _make_span(span_id="a" * 64)
    store.put(span)
    assert store.has(span.id) is True


def test_has_false_for_missing(store: Store) -> None:
    """has returns False for a span that was never put."""
    assert store.has("f" * 64) is False


def test_put_idempotent_no_error(store: Store) -> None:
    """put on the same span twice does not raise and returns the id."""
    span = _make_span(span_id="a" * 64)
    assert store.put(span) == span.id
    assert store.put(span) == span.id


def test_put_dedup_does_not_grow_file(store: Store) -> None:
    """put twice on the same id leaves the JSONL file at exactly one line."""
    span = _make_span(span_id="a" * 64)
    store.put(span)
    span_path = store.root / "spans" / span.id[:2] / f"{span.id}.jsonl"
    size_first = span_path.stat().st_size
    with span_path.open(encoding="utf-8") as f:
        lines_first = sum(1 for _ in f)
    # Second put.
    store.put(span)
    size_second = span_path.stat().st_size
    with span_path.open(encoding="utf-8") as f:
        lines_second = sum(1 for _ in f)
    assert size_first == size_second, "file grew on duplicate put"
    assert lines_first == lines_second == 1, "JSONL should have exactly one line"


def test_put_uses_append_mode(store: Store) -> None:
    """The file path matches the content-addressed layout."""
    span = _make_span(span_id="abcdef" + "0" * 58)
    store.put(span)
    expected_dir = store.root / "spans" / "ab"
    expected_file = expected_dir / f"{span.id}.jsonl"
    assert expected_file.is_file()


# ---------------------------------------------------------------------------
# SQLite index
# ---------------------------------------------------------------------------


def test_index_has_table(tmp_path: Path) -> None:
    """index.sqlite has the spans table with the expected columns."""
    root = tmp_path / ".clew"
    store = Store(root)
    span = _make_span(span_id="a" * 64)
    store.put(span)
    with sqlite3.connect(root / "index.sqlite") as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(spans)").fetchall()]
    assert "id" in cols
    assert "trace_id" in cols
    assert "type" in cols
    assert "name" in cols
    assert "started_at" in cols
    assert "ended_at" in cols
    assert "status" in cols
    assert "parent_ids" in cols
    assert "content_hash" in cols


def test_index_rebuild_from_jsonl(tmp_path: Path) -> None:
    """Deleting index.sqlite triggers a rebuild from the JSONL files on reopen."""
    root = tmp_path / ".clew"
    store = Store(root)
    spans = [
        _make_span(span_id="a" * 64, trace_id="t" * 64),
        _make_span(span_id="b" * 64, trace_id="t" * 64, name="other"),
    ]
    for s in spans:
        store.put(s)
    (root / "index.sqlite").unlink()
    # Re-open; the index must be rebuilt.
    Store(root)
    with sqlite3.connect(root / "index.sqlite") as conn:
        rows = conn.execute("SELECT id FROM spans ORDER BY id").fetchall()
    assert [r[0] for r in rows] == [s.id for s in spans]


# ---------------------------------------------------------------------------
# iter_spans / iter_traces
# ---------------------------------------------------------------------------


def test_iter_spans_no_filter(store: Store) -> None:
    """iter_spans() yields every span in the store."""
    spans = [
        _make_span(span_id="a" * 64, name="a"),
        _make_span(span_id="b" * 64, name="b"),
        _make_span(span_id="c" * 64, name="c"),
    ]
    for s in spans:
        store.put(s)
    seen = {s.id for s in store.iter_spans()}
    assert seen == {s.id for s in spans}


def test_iter_spans_by_trace_id(store: Store) -> None:
    """iter_spans(trace_id) returns only spans for that trace."""
    trace_a = "a" * 64
    trace_b = "b" * 64
    span_a1 = _make_span(span_id="1" * 64, trace_id=trace_a, name="a1")
    span_a2 = _make_span(span_id="2" * 64, trace_id=trace_a, name="a2")
    span_b1 = _make_span(span_id="3" * 64, trace_id=trace_b, name="b1")
    for s in (span_a1, span_a2, span_b1):
        store.put(s)
    got_a = sorted(s.name for s in store.iter_spans(trace_id=trace_a))
    got_b = sorted(s.name for s in store.iter_spans(trace_id=trace_b))
    assert got_a == ["a1", "a2"]
    assert got_b == ["b1"]


def test_iter_traces(store: Store) -> None:
    """iter_traces yields the set of distinct trace_ids."""
    span_a1 = _make_span(span_id="1" * 64, trace_id="a" * 64, name="a1")
    span_a2 = _make_span(span_id="2" * 64, trace_id="a" * 64, name="a2")
    span_b1 = _make_span(span_id="3" * 64, trace_id="b" * 64, name="b1")
    for s in (span_a1, span_a2, span_b1):
        store.put(s)
    assert sorted(store.iter_traces()) == ["a" * 64, "b" * 64]


def test_iter_traces_empty(store: Store) -> None:
    """An empty store yields no trace ids."""
    assert list(store.iter_traces()) == []


# ---------------------------------------------------------------------------
# Security: span id validation
# ---------------------------------------------------------------------------


def test_span_path_rejects_path_traversal(tmp_path: Path) -> None:
    """Span ids that escape the spans/ dir are rejected."""
    store = Store(tmp_path / ".clew")
    for bad in ["../../etc/passwd", "../foo", "foo/bar", "", "abc"]:
        with pytest.raises(ValueError, match="(invalid span id|span id length)"):
            store._span_path(bad)


def test_span_path_rejects_non_hex(tmp_path: Path) -> None:
    """Span ids must be lowercase hex."""
    store = Store(tmp_path / ".clew")
    for bad in ["xyz12345", "ABCDEF12", "12345g"]:
        with pytest.raises(ValueError, match="invalid span id"):
            store._span_path(bad)


def test_span_path_accepts_valid_hex(tmp_path: Path) -> None:
    """A valid 32 or 64 char hex id is accepted."""
    store = Store(tmp_path / ".clew")
    p = store._span_path("ab" * 16)
    assert p.name == ("ab" * 16) + ".jsonl"
    p = store._span_path("cd" * 32)
    assert p.name == ("cd" * 32) + ".jsonl"
