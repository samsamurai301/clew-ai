"""Tests for the v2 occurrence store and integrity boundary."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from clew.core.errors import (
    ConflictingSpanError,
    DuplicateSequenceError,
    SpanIntegrityError,
    StoreManifestError,
    UnsupportedStoreVersion,
)
from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import STORE_VERSION, Store


def _span(
    *,
    span_id: str | None = None,
    trace_id: str | None = None,
    sequence: int = 0,
    parent_ids: list[str] | None = None,
    name: str = "step",
    output: object = "out",
) -> Span:
    now = datetime.now(UTC)
    return Span(
        id=span_id or uuid4().hex,
        trace_id=trace_id or uuid4().hex,
        parent_ids=parent_ids or [],
        sequence=sequence,
        type=SpanType.OBSERVATION,
        name=name,
        attributes={"sequence": sequence},
        input={"value": 1},
        output=output,
        started_at=now,
        ended_at=now,
        status=SpanStatus.OK,
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / ".clew")


def test_fresh_store_writes_v2_manifest_and_layout(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    Store(root)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["format"] == "clew-store"
    assert manifest["version"] == STORE_VERSION == 2
    assert (root / "HEAD").read_text().strip() == "main"
    assert (root / "refs" / "main").read_text().strip() == "0" * 32
    assert (root / "index.sqlite").is_file()


def test_v1_store_is_rejected_without_modification(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    root.mkdir()
    manifest = b'{"version":1,"created_at":"old"}\n'
    (root / "manifest.json").write_bytes(manifest)
    with pytest.raises(UnsupportedStoreVersion, match="Archive or rename"):
        Store(root)
    assert (root / "manifest.json").read_bytes() == manifest
    assert [path.name for path in root.iterdir()] == ["manifest.json"]


def test_unversioned_records_are_never_adopted_automatically(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    record = root / "spans" / "aa" / f"{'a' * 32}.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}")
    with pytest.raises(StoreManifestError, match="no manifest"):
        Store(root)
    assert record.read_text() == "{}"


def test_put_get_and_exact_idempotency(store: Store) -> None:
    span = _span()
    assert store.put(span) == span.id
    path = store.root / "spans" / span.id[:2] / f"{span.id}.json"
    first = path.read_bytes()
    assert store.put(span) == span.id
    assert path.read_bytes() == first
    assert store.get(span.id) == span
    assert store.has(span.id)


def test_same_id_with_different_valid_content_is_conflict(store: Store) -> None:
    first = _span(output="first")
    second = _span(
        span_id=first.id,
        trace_id=first.trace_id,
        sequence=first.sequence,
        output="second",
    )
    store.put(first)
    with pytest.raises(ConflictingSpanError, match="not overwritten"):
        store.put(second)
    assert store.get(first.id).output == "first"


def test_duplicate_sequence_in_one_trace_is_rejected(store: Store) -> None:
    trace_id = uuid4().hex
    store.put(_span(trace_id=trace_id, sequence=0))
    with pytest.raises(DuplicateSequenceError):
        store.put(_span(trace_id=trace_id, sequence=0))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("trace_id", "f" * 32),
        ("sequence", 99),
        ("name", "tampered"),
        ("attributes", {"tampered": True}),
        ("input", {"tampered": True}),
        ("output", "tampered"),
        ("started_at", "2020-01-01T00:00:00Z"),
        ("ended_at", "2030-01-01T00:00:00Z"),
        ("status", "SKIPPED"),
        ("metadata", {"tampered": True}),
    ],
)
def test_get_detects_tampering_of_every_persisted_payload_field(
    store: Store, field: str, replacement: object
) -> None:
    span = _span()
    store.put(span)
    path = store.root / "spans" / span.id[:2] / f"{span.id}.json"
    payload = json.loads(path.read_text())
    payload[field] = replacement
    path.write_text(json.dumps(payload))
    with pytest.raises(SpanIntegrityError):
        store.get(span.id)


def test_get_detects_tampered_hash_and_filename(store: Store) -> None:
    span = _span()
    store.put(span)
    path = store.root / "spans" / span.id[:2] / f"{span.id}.json"
    payload = json.loads(path.read_text())
    payload["content_hash"] = "0" * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(SpanIntegrityError):
        store.get(span.id)


@pytest.mark.parametrize("mutation", ["whitespace", "duplicate-key"])
def test_get_rejects_noncanonical_record_bytes(store: Store, mutation: str) -> None:
    span = _span()
    store.put(span)
    path = store.root / "spans" / span.id[:2] / f"{span.id}.json"
    original = path.read_bytes()
    if mutation == "whitespace":
        path.write_bytes(original + b"\n")
    else:
        text = original.decode()
        path.write_text(text.replace('"input":', '"input":{"ignored":true},"input":', 1))
    with pytest.raises(SpanIntegrityError, match="canonical"):
        store.get(span.id)


def test_missing_index_is_rebuilt_from_verified_json(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    store = Store(root)
    spans = [_span(), _span()]
    for span in spans:
        store.put(span)
    (root / "index.sqlite").unlink()
    reopened = Store(root)
    assert {span.id for span in reopened.iter_spans()} == {span.id for span in spans}


def test_sqlite_uses_wal_busy_timeout_and_sequence_index(store: Store) -> None:
    with sqlite3.connect(store.root / "index.sqlite") as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(spans)")}
    assert mode == "wal"
    assert "idx_spans_trace_sequence" in indexes


def test_unique_temp_files_do_not_collide_or_become_records(store: Store) -> None:
    span = _span()
    shard = store.root / "spans" / span.id[:2]
    shard.mkdir(parents=True)
    (shard / f".{span.id}.json.interrupted.tmp").write_text("partial")
    store.put(span)
    assert [item.id for item in store.iter_spans()] == [span.id]


def _process_writer(root: str, count: int, queue: multiprocessing.Queue[object]) -> None:
    try:
        store = Store(Path(root))
        for _ in range(count):
            store.put(_span())
        queue.put(None)
    except BaseException as exc:  # pragma: no cover - asserted in parent process
        queue.put(repr(exc))


def test_cross_process_writers_preserve_every_occurrence(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    Store(root)
    context = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue[object] = context.Queue()
    processes = [
        context.Process(target=_process_writer, args=(str(root), 10, queue)) for _ in range(3)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert [queue.get(timeout=2) for _ in processes] == [None, None, None]
    reopened = Store(root)
    spans = list(reopened.iter_spans())
    assert len(spans) == 30
    assert len({span.id for span in spans}) == 30


def test_iter_spans_is_ordered_by_sequence_within_trace(store: Store) -> None:
    trace_id = uuid4().hex
    root = _span(trace_id=trace_id, sequence=0)
    child = _span(trace_id=trace_id, sequence=1, parent_ids=[root.id], name="child")
    # Children commonly finish and persist before their parents.
    store.put(child)
    store.put(root)
    assert [span.sequence for span in store.iter_spans(trace_id)] == [0, 1]


def test_malformed_ids_cannot_escape_store(store: Store) -> None:
    with pytest.raises(ValueError, match="invalid span id"):
        store.get("../../etc/passwd")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require Windows privileges")
def test_store_lock_symlink_is_rejected_without_touching_target(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    Store(root)
    lock = root / ".store.lock"
    lock.unlink()
    target = tmp_path / "outside.txt"
    target.write_text("do not modify")
    lock.symlink_to(target)

    with pytest.raises(StoreManifestError, match="store lock"):
        Store(root)

    assert target.read_text() == "do not modify"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require Windows privileges")
def test_symlinked_spans_directory_never_redirects_writes(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "spans").symlink_to(outside, target_is_directory=True)

    with pytest.raises(StoreManifestError, match="span store"):
        Store(root)

    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require Windows privileges")
def test_symlinked_span_record_is_rejected(store: Store, tmp_path: Path) -> None:
    span = _span()
    target = tmp_path / "outside.json"
    target.write_text("{}")
    path = store.root / "spans" / span.id[:2] / f"{span.id}.json"
    path.parent.mkdir()
    path.symlink_to(target)

    with pytest.raises(SpanIntegrityError, match="regular file"):
        store.get(span.id)
    assert target.read_text() == "{}"
