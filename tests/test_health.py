"""Doctor and explicit garbage-collection tests for store v2."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from clew.core.branch import BranchManager
from clew.core.health import GcResult, check_store, gc
from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore


def _span(
    *,
    trace_id: str,
    sequence: int,
    parent_ids: list[str] | None = None,
    name: str = "step",
) -> Span:
    now = datetime.now(UTC)
    return Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=parent_ids or [],
        sequence=sequence,
        type=SpanType.OBSERVATION,
        name=name,
        attributes={},
        input="x",
        output="y",
        started_at=now,
        ended_at=now,
        status=SpanStatus.OK,
    )


def _seed(root: Path) -> tuple[TraceStore, Span, Span]:
    store = Store(root)
    trace_store = TraceStore(store)
    trace_id = uuid4().hex
    root_span = _span(trace_id=trace_id, sequence=0, name="root")
    child = _span(
        trace_id=trace_id,
        sequence=1,
        parent_ids=[root_span.id],
        name="child",
    )
    trace_store.add_span(child)
    trace_store.add_span(root_span)
    BranchManager(trace_store).move("main", child.id)
    return trace_store, root_span, child


def test_healthy_store_has_no_issues(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed(root)
    report = check_store(root)
    assert report.healthy
    assert report.issues == ()
    assert report.span_files == report.indexed_spans == 2
    assert report.head == "main"


def test_doctor_reports_malformed_manifest_without_rewriting_it(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    Store(root)
    malformed = b"{not json"
    (root / "manifest.json").write_bytes(malformed)
    report = check_store(root)
    assert not report.healthy
    assert report.errors[0].code == "corrupt-manifest"
    assert (root / "manifest.json").read_bytes() == malformed


def test_doctor_reports_unsupported_v1_without_migration(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    root.mkdir()
    manifest = b'{"version":1}\n'
    (root / "manifest.json").write_bytes(manifest)
    report = check_store(root)
    assert not report.healthy
    assert {issue.code for issue in report.errors} == {
        "unknown-format",
        "unsupported-version",
    }
    assert (root / "manifest.json").read_bytes() == manifest


def test_doctor_does_not_initialize_a_missing_store(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    report = check_store(root)
    assert not report.healthy
    assert report.errors[0].code == "missing-manifest"
    assert not root.exists()


@pytest.mark.parametrize(
    ("manifest", "expected_code"),
    [
        ([], "bad-manifest"),
        ({"format": "unknown", "version": 2}, "unknown-format"),
        ({"format": "clew-store"}, "missing-version"),
        ({"format": "clew-store", "version": "2"}, "bad-version"),
        ({"format": "clew-store", "version": 99}, "unsupported-version"),
    ],
)
def test_doctor_reports_manifest_contract_errors_without_writes(
    tmp_path: Path,
    manifest: object,
    expected_code: str,
) -> None:
    root = tmp_path / ".clew"
    root.mkdir()
    payload = json.dumps(manifest).encode()
    (root / "manifest.json").write_bytes(payload)
    report = check_store(root)
    assert expected_code in {issue.code for issue in report.errors}
    assert (root / "manifest.json").read_bytes() == payload
    assert not (root / ".store.lock").exists()


def test_doctor_reports_dangling_and_malformed_refs(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed(root)
    (root / "refs" / "main").write_text("f" * 32)
    (root / "refs" / "bad").write_text("../../escape")
    report = check_store(root)
    codes = {issue.code for issue in report.errors}
    assert "dangling-ref" in codes
    assert "malformed-ref" in codes


@pytest.mark.parametrize(
    ("head_content", "expected_code"),
    [
        (None, "missing-head"),
        ("", "empty-head"),
        ("missing-branch\n", "dangling-head"),
    ],
)
def test_doctor_reports_head_contract_errors(
    tmp_path: Path,
    head_content: str | None,
    expected_code: str,
) -> None:
    root = tmp_path / ".clew"
    _seed(root)
    head = root / "HEAD"
    if head_content is None:
        head.unlink()
    else:
        head.write_text(head_content)
    report = check_store(root)
    assert expected_code in {issue.code for issue in report.issues}


def test_doctor_reports_empty_ref(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed(root)
    (root / "refs" / "empty").write_text("")
    report = check_store(root)
    assert "empty-ref" in {issue.code for issue in report.warnings}


def test_doctor_reports_missing_indexed_record(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _, _, child = _seed(root)
    path = root / "spans" / child.id[:2] / f"{child.id}.json"
    path.unlink()
    report = check_store(root)
    codes = {issue.code for issue in report.errors}
    assert "missing-file" in codes


def test_doctor_detects_field_tampering(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _, root_span, _ = _seed(root)
    path = root / "spans" / root_span.id[:2] / f"{root_span.id}.json"
    payload = json.loads(path.read_text())
    payload["output"] = "tampered"
    path.write_text(json.dumps(payload))
    report = check_store(root)
    assert not report.healthy
    assert report.errors[0].code == "invalid-span"
    assert "content_hash" in report.errors[0].message


def test_gc_dry_run_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    trace_store, _, _ = _seed(root)
    orphan = _span(trace_id=uuid4().hex, sequence=0, name="orphan")
    trace_store.add_span(orphan)
    result = gc(root, dry_run=True, min_age_seconds=0)
    path = root / "spans" / orphan.id[:2] / f"{orphan.id}.json"
    assert isinstance(result, GcResult)
    assert orphan.id in result.deleted_ids
    assert path.exists()


def test_gc_deletes_only_unreferenced_trace_and_rebuilds_index(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    trace_store, root_span, child = _seed(root)
    orphan = _span(trace_id=uuid4().hex, sequence=0, name="orphan")
    trace_store.add_span(orphan)
    result = gc(root, min_age_seconds=0)
    assert orphan.id in result.deleted_ids
    reopened = Store(root)
    assert not reopened.has(orphan.id)
    assert reopened.has(root_span.id)
    assert reopened.has(child.id)
    assert check_store(root).healthy


def test_gc_retains_recent_unreferenced_spans_by_default(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    trace_store, _, _ = _seed(root)
    orphan = _span(trace_id=uuid4().hex, sequence=0, name="active-write")
    trace_store.add_span(orphan)
    result = gc(root)
    assert orphan.id not in result.deleted_ids
    assert Store(root).has(orphan.id)


def test_doctor_does_not_rebuild_a_missing_index(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    _seed(root)
    index = root / "index.sqlite"
    index.unlink()
    report = check_store(root)
    assert not index.exists()
    assert "corrupt-index" in {issue.code for issue in report.errors}
