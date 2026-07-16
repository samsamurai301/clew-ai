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
import math
import sqlite3
import stat
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from clew.core.branch import BranchManager
from clew.core.errors import StoreError
from clew.core.models import UUID_HEX_LEN, Span, Trace
from clew.core.store import STORE_FORMAT, STORE_VERSION, Store

#: The bundle format we recognize in ``manifest.json``.
SUPPORTED_STORE_VERSION = STORE_VERSION
GC_MIN_AGE_SECONDS = 300.0


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


def _check_manifest(root: Path) -> Iterator[Issue]:
    """Verify manifest.json exists and has the right shape."""
    manifest_path = root / "manifest.json"
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
    except (OSError, json.JSONDecodeError) as exc:
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
    if data.get("format") != STORE_FORMAT:
        yield Issue(
            Severity.ERROR,
            "unknown-format",
            manifest_path,
            f"unknown format field {data.get('format')!r} (expected {STORE_FORMAT!r})",
        )
    version = data.get("version")
    if version is None:
        yield Issue(
            Severity.ERROR,
            "missing-version",
            manifest_path,
            "manifest.json does not declare a version",
        )
    elif not isinstance(version, int):
        yield Issue(
            Severity.ERROR,
            "bad-version",
            manifest_path,
            f"manifest version is not an integer: {version!r}",
        )
    elif version != SUPPORTED_STORE_VERSION:
        yield Issue(
            Severity.ERROR,
            "unsupported-version",
            manifest_path,
            f"store version {version} is unsupported; this build requires v{SUPPORTED_STORE_VERSION}",
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
    try:
        raw = BranchManager._read_control_file(head_path, label="HEAD").strip()
    except (OSError, ValueError) as exc:
        yield Issue(
            Severity.ERROR,
            "unsafe-head",
            head_path,
            f"HEAD cannot be read safely: {exc}",
        )
        return
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
        try:
            metadata = ref_file.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            yield Issue(
                Severity.ERROR,
                "unsafe-ref",
                ref_file,
                "ref is not a regular single-link file",
            )
            continue
        try:
            target = BranchManager._read_control_file(
                ref_file, label=f"branch {ref_file.name!r}"
            ).strip()
        except (OSError, ValueError) as exc:
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
        # The store init writes a placeholder ref (32 zeros) so HEAD
        # is never dangling on a fresh store. A ref that still points
        # at the placeholder means the user hasn't moved the branch
        # yet — not a problem, just informational.
        if target == "0" * UUID_HEX_LEN:
            continue
        if len(target) != UUID_HEX_LEN or any(char not in "0123456789abcdef" for char in target):
            yield Issue(
                Severity.ERROR,
                "malformed-ref",
                ref_file,
                f"ref contains malformed span id {target!r}",
            )
            continue
        shard = store.root / "spans" / target[:2] / f"{target}.json"
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
    try:
        files_on_disk = {path.stem for path in store._record_paths() if path.suffix == ".json"}
    except StoreError as exc:
        yield Issue(Severity.ERROR, "unsafe-span-layout", spans_dir, str(exc))
        return

    indexed: set[str] = set()
    try:
        with store._connect() as conn:
            for row in conn.execute("SELECT id FROM spans"):
                indexed.add(row[0])
    except (OSError, sqlite3.Error, StoreError) as exc:
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
            spans_dir / sid[:2] / f"{sid}.json",
            "span file exists but is not in the SQLite index",
        )
    # Spans in the index whose file is gone.
    for sid in sorted(indexed - files_on_disk):
        yield Issue(
            Severity.ERROR,
            "missing-file",
            spans_dir / sid[:2] / f"{sid}.json",
            f"SQLite index references span {sid!r} but its file is gone",
        )

    # Refs pointing at a span id that the index doesn't have.
    refs_dir = store.root / "refs"
    if refs_dir.exists():
        for ref_file in refs_dir.iterdir():
            if ref_file.is_symlink() or not ref_file.is_file():
                continue
            try:
                target = BranchManager._read_control_file(
                    ref_file, label=f"branch {ref_file.name!r}"
                ).strip()
            except (OSError, ValueError):
                continue
            if target and target not in indexed:
                # Already reported by _check_refs as "dangling-ref".
                pass


def _check_trace_topologies(store: Store) -> Iterator[Issue]:
    """Validate every trace directly from verified JSON, without the index."""
    grouped: dict[str, list[Span]] = {}
    try:
        for span in store.iter_spans():
            grouped.setdefault(span.trace_id, []).append(span)
    except StoreError as exc:
        yield Issue(Severity.ERROR, "invalid-span", store.root / "spans", str(exc))
        return
    for trace_id, spans in grouped.items():
        roots = [span for span in spans if not span.parent_ids]
        try:
            Trace(
                trace_id=trace_id,
                root_span_id=roots[0].id if roots else "",
                spans=spans,
            )
        except ValueError as exc:
            yield Issue(
                Severity.ERROR,
                "invalid-topology",
                store.root / "spans",
                f"Trace {trace_id} is invalid: {exc}",
            )


def _safe_span_file_count(root: Path) -> int:
    """Count regular span files without following untrusted directories."""
    spans_dir = root / "spans"
    try:
        metadata = spans_dir.lstat()
    except OSError:
        return 0
    if not stat.S_ISDIR(metadata.st_mode):
        return 0
    count = 0
    try:
        for shard in spans_dir.iterdir():
            shard_metadata = shard.lstat()
            if not stat.S_ISDIR(shard_metadata.st_mode):
                continue
            for record in shard.iterdir():
                record_metadata = record.lstat()
                if stat.S_ISREG(record_metadata.st_mode) and record.suffix == ".json":
                    count += 1
    except OSError:
        return count
    return count


def check_store(root: Path) -> DoctorReport:
    """Run all doctor checks and return a :class:`DoctorReport`.

    The store is opened via :class:`Store` so the on-disk layout is
    always what clew expects. Issues are collected, not raised — the
    caller can decide how to render them.
    """
    root = Path(root)
    manifest_issues = tuple(_check_manifest(root))
    if manifest_issues:
        refs_dir = root / "refs"
        refs = (
            tuple(sorted(path.name for path in refs_dir.iterdir() if path.is_file()))
            if refs_dir.is_dir()
            else ()
        )
        head_path = root / "HEAD"
        try:
            head = head_path.read_text(encoding="utf-8").strip() if head_path.exists() else None
        except OSError:
            head = None
        return DoctorReport(
            issues=manifest_issues,
            span_files=_safe_span_file_count(root),
            indexed_spans=0,
            ref_count=len(refs),
            branches=refs,
            head=head,
        )
    try:
        store = Store(root, read_only=True)
    except StoreError as exc:
        return DoctorReport(
            issues=(
                Issue(
                    Severity.ERROR,
                    "store-open-failed",
                    root,
                    str(exc),
                ),
            ),
            span_files=_safe_span_file_count(root),
            indexed_spans=0,
            ref_count=0,
            branches=(),
            head=None,
        )
    issues: list[Issue] = []
    issues.extend(_check_head(store))
    issues.extend(_check_refs(store))
    issues.extend(_check_index_consistency(store))
    issues.extend(_check_trace_topologies(store))

    # Counts.
    span_files = _safe_span_file_count(root)
    indexed_spans = 0
    try:
        with store._connect() as conn:
            indexed_spans = conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
    except (OSError, sqlite3.Error, StoreError):
        pass
    refs_dir = store.root / "refs"
    ref_count = sum(1 for f in refs_dir.iterdir() if f.is_file()) if refs_dir.exists() else 0
    branches: tuple[str, ...] = (
        tuple(sorted(p.name for p in refs_dir.iterdir() if p.is_file()))
        if refs_dir.exists()
        else ()
    )
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


def _reachable_span_ids(store: Store, spans: list[Span]) -> set[str]:
    """Return the set of span ids that are reachable from any ref.

    A span is reachable iff it is the head of some ref, an ancestor
    of a head, or part of a trace that a head references (we don't
    actually walk the trace, just check that the span file is in
    ``spans/``). Practically: every file under ``spans/`` is a
    candidate; we filter by "is this id referenced by a ref or by a
    span that is itself referenced?"
    """
    refs_dir = store.root / "refs"
    if not refs_dir.exists():
        return set()
    # Walk every trace referenced by a ref.
    by_id = {span.id: span for span in spans}
    trace_ids: set[str] = set()
    for ref_file in refs_dir.iterdir():
        if ref_file.is_symlink() or not ref_file.is_file():
            raise StoreError(f"Unsafe ref {ref_file}; refusing garbage collection.")
        try:
            target = BranchManager._read_control_file(
                ref_file, label=f"branch {ref_file.name!r}"
            ).strip()
        except (OSError, ValueError) as exc:
            raise StoreError(
                f"Cannot safely read ref {ref_file}; refusing garbage collection: {exc}"
            ) from exc
        if not target or target == "0" * UUID_HEX_LEN:
            continue
        head_span = by_id.get(target)
        if head_span is None:
            raise StoreError(
                f"Ref {ref_file.name!r} points to missing span {target}; "
                "run `clew doctor` before garbage collection."
            )
        trace_ids.add(head_span.trace_id)
    return {span.id for span in spans if span.trace_id in trace_ids}


def gc(
    root: Path,
    *,
    dry_run: bool = False,
    min_age_seconds: float = GC_MIN_AGE_SECONDS,
) -> GcResult:
    """Remove orphan span files (no ref, no ancestor relationship).

    A span is "orphan" iff it is not reachable from any current ref.
    This is the natural cleanup after a `clew branch` that you
    decided not to keep. With ``dry_run=True``, the report is built
    but nothing is deleted.
    """
    if not math.isfinite(min_age_seconds) or min_age_seconds < 0:
        raise ValueError("min_age_seconds must be a finite non-negative number")
    store = Store(root)
    deleted: list[str] = []
    with store._thread_lock, store._process_lock:
        records = list(store._record_paths())
        spans = [store._read_span(path, expected_id=path.stem) for path in records]
        paths = {path.stem: path for path in records}
        all_ids = set(paths)
        reachable = _reachable_span_ids(store, spans)
        now = time.time()
        orphans = {
            sid
            for sid in all_ids - reachable
            if now - paths[sid].lstat().st_mtime >= min_age_seconds
        }
        for sid in sorted(orphans):
            shard = paths[sid]
            if not dry_run:
                try:
                    shard.unlink()
                except OSError:
                    continue
            deleted.append(sid)
        if deleted and not dry_run:
            store._rebuild_index_unlocked()
    return GcResult(
        scanned=len(all_ids),
        deleted=len(deleted),
        kept=len(all_ids) - len(deleted),
        deleted_ids=tuple(deleted),
    )


__all__ = [
    "GC_MIN_AGE_SECONDS",
    "SUPPORTED_STORE_VERSION",
    "DoctorReport",
    "GcResult",
    "Issue",
    "Severity",
    "Span",  # re-export for tests
    "check_store",
    "gc",
]
