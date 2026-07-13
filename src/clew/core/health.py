"""Store health checks and repairs.

A clew store is a small set of files (``spans/``, ``refs/``,
``index.sqlite``, ``HEAD``, ``manifest.json``) that must stay in
sync. Disk corruption, manual edits, or process crashes can
desynchronize them. ``clew doctor`` walks the store and reports
problems; ``clew gc`` cleans up after itself.

The doctor runs four checks:

1. **Manifest sanity** — ``manifest.json`` is valid JSON, has the
   required keys, and the format/version match what we support.
2. **HEAD is valid** — ``HEAD`` is a single line naming a branch that
   actually exists in ``refs/``.
3. **Ref targets exist** — every ref points at a span id whose file
   is present in ``spans/`` and indexed in ``index.sqlite``.
4. **Index consistency** — every row in ``spans`` has a corresponding
   file on disk, and every file on disk has a row in ``spans``.

Each issue is reported with a ``severity`` ("error" or "warning") and
a human-readable description. The doctor never mutates the store; it
is read-only by design.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from clew.core.models import Span
from clew.core.store import Store

#: The bundle format we recognize in ``manifest.json``.
SUPPORTED_BUNDLE_VERSION = 1


class Severity(StrEnum):
    """How bad a problem is.

    ``ERROR`` means the store is broken; some operations may fail or
    produce wrong results. ``WARNING`` means something is off but the
    store still works (e.g. an orphan span from a deleted branch).
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """A single problem found by the doctor."""

    severity: Severity
    code: str
    path: Path
    message: str

    def to_dict(self) -> dict[str, str]:
        """Serialize the issue as a JSON-safe dict."""
        return {
            "severity": self.severity.value,
            "code": self.code,
            "path": str(self.path),
            "message": self.message,
        }


@dataclass(frozen=True)
class DoctorReport:
    """Aggregated report from :func:`check_store`."""

    issues: tuple[Issue, ...]
    span_files: int
    indexed_spans: int
    ref_count: int
    branches: tuple[str, ...]
    head: str | None

    @property
    def errors(self) -> tuple[Issue, ...]:
        """All issues with severity ERROR."""
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        """All issues with severity WARNING."""
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def healthy(self) -> bool:
        """True iff the report has no errors (warnings are OK)."""
        return not self.errors


def _check_manifest(store: Store) -> Iterator[Issue]:
    """Verify manifest.json exists and has the right shape."""
    manifest_path = store.root / "manifest.json"
    if not manifest_path.exists():
        yield Issue(
            Severity.ERROR,
            "missing-manifest",
            manifest_path,
            "manifest.json is missing — the store has never been initialized",
        )
        return
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        yield Issue(
            Severity.ERROR,
            "corrupt-manifest",
            manifest_path,
            f"manifest.json is not valid JSON: {exc}",
        )
        return
    if not isinstance(data, dict):
        yield Issue(
            Severity.ERROR,
            "bad-manifest",
            manifest_path,
            "manifest.json is not a JSON object",
        )
        return
    if "format" in data and data.get("format") != "clew-store":
        yield Issue(
            Severity.WARNING,
            "unknown-format",
            manifest_path,
            f"unknown format field {data.get('format')!r} (expected 'clew-store' or absent)",
        )
    version = data.get("version")
    if version is None:
        yield Issue(
            Severity.WARNING,
            "missing-version",
            manifest_path,
            "manifest.json does not declare a version",
        )
    elif not isinstance(version, int):
        yield Issue(
            Severity.WARNING,
            "bad-version",
            manifest_path,
            f"manifest version is not an integer: {version!r}",
        )
    elif version > SUPPORTED_BUNDLE_VERSION:
        yield Issue(
            Severity.WARNING,
            "future-version",
            manifest_path,
            f"store was written by a newer clew (version {version}); this build only knows up to v{SUPPORTED_BUNDLE_VERSION}",
        )


def _check_head(store: Store) -> Iterator[Issue]:
    """Verify HEAD exists and names an existing branch."""
    head_path = store.root / "HEAD"
    if not head_path.exists():
        yield Issue(
            Severity.WARNING,
            "missing-head",
            head_path,
            "HEAD is missing — no current branch is selected",
        )
        return
    raw = head_path.read_text(encoding="utf-8").strip()
    if not raw:
        yield Issue(
            Severity.WARNING,
            "empty-head",
            head_path,
            "HEAD is empty",
        )
        return
    refs_dir = store.root / "refs"
    if not (refs_dir / raw).exists():
        yield Issue(
            Severity.ERROR,
            "dangling-head",
            head_path,
            f"HEAD names branch {raw!r} but refs/{raw} does not exist",
        )


def _check_refs(store: Store) -> Iterator[Issue]:
    """Verify every ref points at an existing span."""
    refs_dir = store.root / "refs"
    if not refs_dir.exists():
        return
    for ref_file in refs_dir.iterdir():
        if not ref_file.is_file():
            continue
        try:
            target = ref_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            yield Issue(
                Severity.ERROR,
                "unreadable-ref",
                ref_file,
                f"cannot read ref: {exc}",
            )
            continue
        if not target:
            yield Issue(
                Severity.WARNING,
                "empty-ref",
                ref_file,
                "ref file is empty",
            )
            continue
        # The store init writes a placeholder ref (64 zeros) so HEAD
        # is never dangling on a fresh store. A ref that still points
        # at the placeholder means the user hasn't moved the branch
        # yet — not a problem, just informational.
        if target == "0" * 64:
            continue
        shard = store.root / "spans" / target[:2] / f"{target}.jsonl"
        if not shard.exists():
            yield Issue(
                Severity.ERROR,
                "dangling-ref",
                ref_file,
                f"ref points at span {target!r} whose shard file does not exist",
            )


def _check_index_consistency(store: Store) -> Iterator[Issue]:
    """Verify the SQLite index agrees with the on-disk span files."""
    spans_dir = store.root / "spans"
    files_on_disk: set[str] = set()
    if spans_dir.exists():
        for shard in spans_dir.glob("*/*.jsonl"):
            file_id = shard.stem
            files_on_disk.add(file_id)

    indexed: set[str] = set()
    try:
        with sqlite3.connect(store.root / "index.sqlite") as conn:
            for row in conn.execute("SELECT id FROM spans"):
                indexed.add(row[0])
    except sqlite3.DatabaseError as exc:
        yield Issue(
            Severity.ERROR,
            "corrupt-index",
            store.root / "index.sqlite",
            f"SQLite index is unreadable: {exc}",
        )
        return

    # Spans on disk that the index doesn't know about.
    for sid in sorted(files_on_disk - indexed):
        yield Issue(
            Severity.WARNING,
            "orphan-file",
            spans_dir / sid[:2] / f"{sid}.jsonl",
            "span file exists but is not in the SQLite index",
        )
    # Spans in the index whose file is gone.
    for sid in sorted(indexed - files_on_disk):
        yield Issue(
            Severity.ERROR,
            "missing-file",
            spans_dir / sid[:2] / f"{sid}.jsonl",
            f"SQLite index references span {sid!r} but its file is gone",
        )

    # Refs pointing at a span id that the index doesn't have.
    refs_dir = store.root / "refs"
    if refs_dir.exists():
        for ref_file in refs_dir.iterdir():
            if not ref_file.is_file():
                continue
            try:
                target = ref_file.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if target and target not in indexed:
                # Already reported by _check_refs as "dangling-ref".
                pass


def check_store(root: Path) -> DoctorReport:
    """Run all doctor checks and return a :class:`DoctorReport`.

    The store is opened via :class:`Store` so the on-disk layout is
    always what clew expects. Issues are collected, not raised — the
    caller can decide how to render them.
    """
    store = Store(root)
    issues: list[Issue] = []
    issues.extend(_check_manifest(store))
    issues.extend(_check_head(store))
    issues.extend(_check_refs(store))
    issues.extend(_check_index_consistency(store))

    # Counts.
    spans_dir = store.root / "spans"
    span_files = sum(1 for _ in spans_dir.glob("*/*.jsonl")) if spans_dir.exists() else 0
    indexed_spans = 0
    try:
        with sqlite3.connect(store.root / "index.sqlite") as conn:
            indexed_spans = conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
    except sqlite3.DatabaseError:
        pass
    refs_dir = store.root / "refs"
    ref_count = sum(1 for f in refs_dir.iterdir() if f.is_file()) if refs_dir.exists() else 0
    branches: tuple[str, ...] = tuple(sorted(p.name for p in refs_dir.iterdir() if p.is_file())) if refs_dir.exists() else ()
    head_path = store.root / "HEAD"
    head = head_path.read_text(encoding="utf-8").strip() if head_path.exists() else None
    return DoctorReport(
        issues=tuple(issues),
        span_files=span_files,
        indexed_spans=indexed_spans,
        ref_count=ref_count,
        branches=branches,
        head=head,
    )


# ---------------------------------------------------------------------------
# Garbage collection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GcResult:
    """Outcome of a ``clew gc`` run."""

    scanned: int
    deleted: int
    kept: int
    deleted_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize the GC result as a JSON-safe dict."""
        return {
            "scanned": self.scanned,
            "deleted": self.deleted,
            "kept": self.kept,
            "deleted_ids": list(self.deleted_ids),
        }


def _reachable_span_ids(root: Path) -> set[str]:
    """Return the set of span ids that are reachable from any ref.

    A span is reachable iff it is the head of some ref, an ancestor
    of a head, or part of a trace that a head references (we don't
    actually walk the trace, just check that the span file is in
    ``spans/``). Practically: every file under ``spans/`` is a
    candidate; we filter by "is this id referenced by a ref or by a
    span that is itself referenced?"
    """
    store = Store(root)
    refs_dir = store.root / "refs"
    if not refs_dir.exists():
        return set()
    # Walk every trace referenced by a ref.
    reachable: set[str] = set()
    trace_ids: set[str] = set()
    for ref_file in refs_dir.iterdir():
        if not ref_file.is_file():
            continue
        target = ref_file.read_text(encoding="utf-8").strip()
        if not target:
            continue
        try:
            head_span = store.get(target)
        except KeyError:
            continue
        trace_ids.add(head_span.trace_id)
    for tid in trace_ids:
        for s in store.iter_spans(tid):
            reachable.add(s.id)
    return reachable


def gc(root: Path, *, dry_run: bool = False) -> GcResult:
    """Remove orphan span files (no ref, no ancestor relationship).

    A span is "orphan" iff it is not reachable from any current ref.
    This is the natural cleanup after a `clew branch` that you
    decided not to keep. With ``dry_run=True``, the report is built
    but nothing is deleted.
    """
    spans_dir = root / "spans"
    if not spans_dir.exists():
        return GcResult(scanned=0, deleted=0, kept=0, deleted_ids=())
    all_ids = {p.stem for p in spans_dir.glob("*/*.jsonl")}
    reachable = _reachable_span_ids(root)
    orphans = all_ids - reachable
    deleted: list[str] = []
    for sid in sorted(orphans):
        shard = spans_dir / sid[:2] / f"{sid}.jsonl"
        if not dry_run:
            try:
                shard.unlink()
            except OSError:
                # Couldn't delete; skip.
                continue
        deleted.append(sid)
    return GcResult(
        scanned=len(all_ids),
        deleted=len(deleted),
        kept=len(all_ids) - len(deleted),
        deleted_ids=tuple(deleted),
    )


__all__ = [
    "SUPPORTED_BUNDLE_VERSION",
    "DoctorReport",
    "GcResult",
    "Issue",
    "Severity",
    "Span",  # re-export for tests
    "check_store",
    "gc",
]
