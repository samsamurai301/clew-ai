"""Crash-safe v2 store for finalized Clew spans.

The JSON records are the source of truth. SQLite is a rebuildable query index;
it is never trusted as the only copy of a trace.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from clew.core.errors import (
    ConflictingSpanError,
    DuplicateSequenceError,
    SpanIntegrityError,
    StoreManifestError,
    UnsupportedStoreVersion,
)
from clew.core.models import UUID_HEX_LEN, Span
from clew.utils.hash import canonical_json, span_hash

STORE_FORMAT = "clew-store"
STORE_VERSION = 2
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL,
    status TEXT NOT NULL,
    parent_ids TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_spans_trace_sequence
    ON spans(trace_id, sequence);
CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);
"""

_UPSERT_SPAN = """
INSERT INTO spans (
    id, trace_id, sequence, type, name, started_at, ended_at,
    status, parent_ids, content_hash
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    trace_id=excluded.trace_id,
    sequence=excluded.sequence,
    type=excluded.type,
    name=excluded.name,
    started_at=excluded.started_at,
    ended_at=excluded.ended_at,
    status=excluded.status,
    parent_ids=excluded.parent_ids,
    content_hash=excluded.content_hash
"""


def _iso_utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fsync_directory(path: Path) -> None:
    """Best-effort durability for a completed atomic rename."""
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_exclusive(path: Path, payload: bytes) -> None:
    """Create ``path`` without following a destination symlink."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise


def _lstat(path: Path) -> os.stat_result | None:
    """Return metadata without following links, including broken symlinks."""
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _require_directory(path: Path, *, label: str, create: bool = False) -> None:
    """Create or validate a real directory without accepting a symlink."""
    metadata = _lstat(path)
    if metadata is None and create:
        path.mkdir(parents=True, exist_ok=True)
        metadata = _lstat(path)
    if metadata is None:
        raise StoreManifestError(f"Missing {label} directory at {path}.")
    if not stat.S_ISDIR(metadata.st_mode):
        raise StoreManifestError(
            f"Unsafe {label} path at {path}: expected a real directory, not a "
            "symlink or other file type. No data was modified."
        )


def _require_regular_file(path: Path, *, label: str) -> os.stat_result:
    """Validate one regular, single-link file without following symlinks."""
    metadata = _lstat(path)
    if metadata is None:
        raise FileNotFoundError(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise StoreManifestError(
            f"Unsafe {label} at {path}: expected a regular file with one link. "
            "No data was modified."
        )
    return metadata


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    """Read a file after link/type checks and verify it was not swapped."""
    before = _require_regular_file(path, label=label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise StoreManifestError(f"Cannot safely open {label} at {path}: {exc}.") from exc
    try:
        after = os.fstat(fd)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise StoreManifestError(
                f"Unsafe {label} at {path}: it changed while being opened. No data was modified."
            )
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            return stream.read()
    finally:
        if fd >= 0:
            os.close(fd)


class _SafeFileLock:
    """Small cross-process lock that never truncates or follows a lock symlink."""

    def __init__(self, path: Path, *, timeout: float) -> None:
        self.path = path
        self.timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> _SafeFileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()

    def acquire(self) -> None:
        if self._fd is not None:
            raise RuntimeError(f"Lock {self.path} is already held by this object")
        existing = _lstat(self.path)
        if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
            raise StoreManifestError(
                f"Unsafe store lock at {self.path}: expected a regular file with one link. "
                "No data was modified."
            )

        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise StoreManifestError(
                f"Cannot safely open store lock at {self.path}: {exc}. No data was modified."
            ) from exc

        try:
            opened = os.fstat(fd)
            current = _lstat(self.path)
            if (
                current is None
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                or (
                    existing is not None
                    and (existing.st_dev, existing.st_ino) != (opened.st_dev, opened.st_ino)
                )
            ):
                raise StoreManifestError(
                    f"Unsafe store lock at {self.path}: it changed while being opened. "
                    "No data was modified."
                )
            self._acquire_fd(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def _acquire_fd(self, fd: int) -> None:
        deadline = time.monotonic() + self.timeout
        if os.name == "nt":  # pragma: no cover - exercised by Windows CI
            import msvcrt

            locking = vars(msvcrt)["locking"]
            nonblocking_lock = vars(msvcrt)["LK_NBLCK"]
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    locking(fd, nonblocking_lock, 1)
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out waiting for store lock {self.path}"
                        ) from None
                    time.sleep(0.05)

        import fcntl

        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for store lock {self.path}") from None
                time.sleep(0.05)

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            if os.name == "nt":  # pragma: no cover - exercised by Windows CI
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                locking = vars(msvcrt)["locking"]
                unlock = vars(msvcrt)["LK_UNLCK"]
                locking(fd, unlock, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class _NoopFileLock:
    """Context manager used by read-only stores without touching the disk."""

    def __enter__(self) -> _NoopFileLock:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class Store:
    """A v2 store rooted at a ``.clew`` directory.

    Opening a v1 store raises :class:`UnsupportedStoreVersion` before any
    existing file is modified. A caller must archive or rename that directory
    and initialize a fresh store explicitly.
    """

    def __init__(self, root: Path, *, read_only: bool = False) -> None:
        self.root = Path(root)
        self._read_only = read_only
        _require_directory(self.root, label="store root", create=not read_only)
        self._spans_dir = self.root / "spans"
        self._refs_dir = self.root / "refs"
        self._db_path = self.root / "index.sqlite"
        self._thread_lock = threading.RLock()
        self._preflight_existing_layout()
        if read_only:
            if _lstat(self.manifest_path) is None:
                raise StoreManifestError(
                    f"Missing Clew store manifest at {self.manifest_path}; "
                    "read-only access cannot initialize a store."
                )
            self._created_store = False
            self._process_lock: _SafeFileLock | _NoopFileLock = _NoopFileLock()
            self._ensure_manifest()
            _require_directory(self._spans_dir, label="span store")
            _require_directory(self._refs_dir, label="reference store")
            self._validate_index_files()
            return

        self._process_lock = _SafeFileLock(
            self.root / ".store.lock", timeout=SQLITE_TIMEOUT_SECONDS
        )
        with self._thread_lock, self._process_lock:
            self._created_store = self._ensure_manifest()
            _require_directory(self._spans_dir, label="span store", create=True)
            _require_directory(self._refs_dir, label="reference store", create=True)
            if self._created_store:
                self._ensure_head()
            self._ensure_index()

    # -- initialization -------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def _preflight_existing_layout(self) -> None:
        """Reject legacy/malformed stores before even creating a lock file."""
        for directory, label in (
            (self._spans_dir, "span store"),
            (self._refs_dir, "reference store"),
        ):
            if _lstat(directory) is not None:
                _require_directory(directory, label=label)
        path = self.manifest_path
        if _lstat(path) is not None:
            try:
                manifest = json.loads(
                    _read_regular_bytes(path, label="store manifest").decode("utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StoreManifestError(
                    f"Malformed Clew store manifest at {path}: {exc}. "
                    "No existing data was modified."
                ) from exc
            if not isinstance(manifest, dict):
                raise StoreManifestError(
                    f"Malformed Clew store manifest at {path}: expected a JSON object. "
                    "No existing data was modified."
                )
            if manifest.get("version") != STORE_VERSION:
                raise UnsupportedStoreVersion(self._unsupported_message(manifest.get("version")))
            return
        records = list(self._record_paths())
        if any(path.suffix == ".jsonl" for path in records):
            raise UnsupportedStoreVersion(self._unsupported_message(1))
        if any(path.suffix == ".json" for path in records):
            raise StoreManifestError(
                f"Clew store {self.root} contains span records but no manifest. "
                "Restore manifest.json from backup or archive the directory; "
                "no data was modified."
            )

    def _ensure_manifest(self) -> bool:
        path = self.manifest_path
        if _lstat(path) is None:
            records = list(self._record_paths())
            legacy_records = [record for record in records if record.suffix == ".jsonl"]
            if legacy_records:
                raise UnsupportedStoreVersion(self._unsupported_message(1))
            unversioned_records = [record for record in records if record.suffix == ".json"]
            if unversioned_records:
                raise StoreManifestError(
                    f"Clew store {self.root} contains span records but no manifest. "
                    "Restore manifest.json from backup or archive the directory; "
                    "no data was modified."
                )
            manifest = {
                "format": STORE_FORMAT,
                "version": STORE_VERSION,
                "created_at": _iso_utc_now(),
            }
            self._atomic_write(path, canonical_json(manifest) + b"\n")
            return True

        try:
            manifest = json.loads(_read_regular_bytes(path, label="store manifest").decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreManifestError(
                f"Malformed Clew store manifest at {path}: {exc}. "
                "Repair the manifest or archive this .clew directory."
            ) from exc
        if not isinstance(manifest, dict):
            raise StoreManifestError(
                f"Malformed Clew store manifest at {path}: expected a JSON object."
            )
        version = manifest.get("version")
        if version != STORE_VERSION:
            raise UnsupportedStoreVersion(self._unsupported_message(version))
        if manifest.get("format") != STORE_FORMAT:
            raise StoreManifestError(
                f"Unsupported store format in {path}: {manifest.get('format')!r}; "
                f"expected {STORE_FORMAT!r}."
            )
        return False

    def _unsupported_message(self, version: object) -> str:
        return (
            f"Clew store {self.root} uses unsupported format version {version!r}; "
            f"Clew 1.1.5 supports only store version {STORE_VERSION}. Archive or "
            "rename the existing .clew directory, then run `clew init` to create "
            "a new store. No existing data was modified."
        )

    def _ensure_head(self) -> None:
        head = self.root / "HEAD"
        if not head.exists():
            self._atomic_write(head, b"main\n")
        default_ref = self._refs_dir / "main"
        if not default_ref.exists():
            self._atomic_write(default_ref, ("0" * UUID_HEX_LEN + "\n").encode())

    def _ensure_index(self) -> None:
        self._validate_index_files()
        rebuild = _lstat(self._db_path) is None
        if not rebuild:
            try:
                with closing(self._connect()) as conn:
                    result = conn.execute("PRAGMA quick_check").fetchone()
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(spans)")}
                rebuild = result != ("ok",) or "sequence" not in columns
            except sqlite3.DatabaseError:
                rebuild = True
        if rebuild:
            self._preserve_bad_index()
            self._rebuild_index_unlocked()
        else:
            self._reconcile_index()

    def _preserve_bad_index(self) -> None:
        if _lstat(self._db_path) is None:
            return
        _require_regular_file(self._db_path, label="SQLite index")
        preserved = self.root / f"index.sqlite.invalid.{uuid.uuid4().hex}"
        os.replace(self._db_path, preserved)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self._db_path}{suffix}")
            if _lstat(sidecar) is not None:
                _require_regular_file(sidecar, label="SQLite index sidecar")
                sidecar.unlink()

    def _rebuild_index_unlocked(self) -> None:
        temp = self.root / f".index.{uuid.uuid4().hex}.sqlite"
        try:
            with closing(sqlite3.connect(temp, timeout=SQLITE_TIMEOUT_SECONDS)) as conn:
                conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
                conn.executescript(_SCHEMA)
                for span in self._iter_files():
                    self._insert_index_row(conn, span)
                conn.commit()
            os.replace(temp, self._db_path)
            _fsync_directory(self.root)
            with closing(self._connect()) as conn:
                conn.executescript(_SCHEMA)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def rebuild_index(self) -> None:
        """Rebuild the SQLite index atomically from verified JSON records."""
        self._assert_writable("rebuild the index")
        with self._thread_lock, self._process_lock:
            self._rebuild_index_unlocked()

    def _reconcile_index(self) -> None:
        spans = list(self._iter_files())
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for span in spans:
                self._insert_index_row(conn, span)
            conn.commit()

    # -- sqlite ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._validate_index_files()
        if self._read_only:
            try:
                _require_regular_file(self._db_path, label="SQLite index")
            except FileNotFoundError as exc:
                raise StoreManifestError(
                    f"SQLite index is missing at {self._db_path}; run a writable Clew "
                    "command to rebuild it from verified span records."
                ) from exc
            uri = f"{self._db_path.resolve().as_uri()}?mode=ro&immutable=1"
            conn = sqlite3.connect(uri, timeout=SQLITE_TIMEOUT_SECONDS, uri=True)
            conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            return conn

        conn = sqlite3.connect(self._db_path, timeout=SQLITE_TIMEOUT_SECONDS)
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _assert_writable(self, operation: str) -> None:
        if self._read_only:
            raise StoreManifestError(f"Cannot {operation}: store {self.root} is open read-only.")

    def _validate_index_files(self) -> None:
        for path, label in (
            (self._db_path, "SQLite index"),
            (Path(f"{self._db_path}-wal"), "SQLite WAL file"),
            (Path(f"{self._db_path}-shm"), "SQLite shared-memory file"),
        ):
            if _lstat(path) is not None:
                _require_regular_file(path, label=label)

    @staticmethod
    def _insert_index_row(conn: sqlite3.Connection, span: Span) -> None:
        try:
            conn.execute(
                _UPSERT_SPAN,
                (
                    span.id,
                    span.trace_id,
                    span.sequence,
                    span.type.value,
                    span.name,
                    span.started_at.timestamp(),
                    span.ended_at.timestamp(),
                    span.status.value,
                    json.dumps(span.parent_ids, separators=(",", ":")),
                    span.content_hash,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "spans.trace_id, spans.sequence" in str(exc):
                raise DuplicateSequenceError(
                    f"Trace {span.trace_id} already contains sequence "
                    f"{span.sequence}; each occurrence must have a unique order."
                ) from exc
            raise

    # -- records --------------------------------------------------------

    def _span_path(self, span_id: str) -> Path:
        if len(span_id) != UUID_HEX_LEN or any(char not in "0123456789abcdef" for char in span_id):
            raise ValueError(
                f"invalid span id {span_id!r}; expected {UUID_HEX_LEN} lowercase "
                "hexadecimal characters"
            )
        return self._spans_dir / span_id[:2] / f"{span_id}.json"

    @staticmethod
    def _serialize_span(span: Span) -> bytes:
        return canonical_json(span.model_dump(mode="json"))

    def _read_span(self, path: Path, *, expected_id: str | None = None) -> Span:
        try:
            payload = _read_regular_bytes(path, label="span record")
            span = Span.model_validate_json(payload)
        except (OSError, StoreManifestError, ValidationError, ValueError) as exc:
            raise SpanIntegrityError(f"Invalid span record {path}: {exc}") from exc
        if expected_id is not None and span.id != expected_id:
            raise SpanIntegrityError(
                f"Span path {path} claims id {span.id}; expected {expected_id}."
            )
        actual = span_hash(span)
        if actual != span.content_hash:
            raise SpanIntegrityError(
                f"Span {span.id} failed integrity verification: expected "
                f"{span.content_hash}, calculated {actual}."
            )
        if payload != self._serialize_span(span):
            raise SpanIntegrityError(
                f"Span {span.id} is not encoded as canonical Clew JSON. "
                "The record may contain duplicate keys, reordered fields, or extra bytes."
            )
        return span

    def _record_paths(self) -> Iterator[Path]:
        if _lstat(self._spans_dir) is None:
            return
        _require_directory(self._spans_dir, label="span store")
        with os.scandir(self._spans_dir) as shards:
            shard_entries = sorted(shards, key=lambda entry: entry.name)
        for shard in shard_entries:
            shard_path = Path(shard.path)
            if shard.is_symlink() or not shard.is_dir(follow_symlinks=False):
                raise StoreManifestError(
                    f"Unsafe span shard at {shard_path}: expected a real directory."
                )
            with os.scandir(shard_path) as records:
                record_entries = sorted(records, key=lambda entry: entry.name)
            for record in record_entries:
                if record.is_symlink():
                    raise StoreManifestError(
                        f"Unsafe span record at {record.path}: symlinks are not allowed."
                    )
                if record.is_file(follow_symlinks=False) and Path(record.name).suffix in {
                    ".json",
                    ".jsonl",
                }:
                    yield Path(record.path)

    def _iter_files(self) -> Iterator[Span]:
        for path in self._record_paths():
            if path.suffix != ".json":
                raise UnsupportedStoreVersion(self._unsupported_message(1))
            yield self._read_span(path, expected_id=path.stem)

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        _require_directory(path.parent, label="record parent", create=True)
        temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            _write_exclusive(temp, payload)
            os.replace(temp, path)
            _fsync_directory(path.parent)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def put(self, span: Span) -> str:
        """Persist one finalized occurrence and verify it before returning.

        Repeating the exact same bytes for an id is idempotent. Reusing that id
        for any different record is an explicit corruption error.
        """
        self._assert_writable("persist a span")
        expected_hash = span_hash(span)
        if span.content_hash != expected_hash:
            raise SpanIntegrityError(
                f"Refusing span {span.id}: content hash is {span.content_hash}, "
                f"but the finalized record hashes to {expected_hash}."
            )
        payload = self._serialize_span(span)
        with self._thread_lock, self._process_lock:
            path = self._span_path(span.id)
            if _lstat(path) is not None:
                try:
                    existing = _read_regular_bytes(path, label="span record")
                except StoreManifestError as exc:
                    raise SpanIntegrityError(str(exc)) from exc
                if existing != payload:
                    raise ConflictingSpanError(
                        f"Span id {span.id} already exists with different content; "
                        "the existing record was not overwritten."
                    )
                self._read_span(path, expected_id=span.id)
                with closing(self._connect()) as conn:
                    self._insert_index_row(conn, span)
                    conn.commit()
                return span.id

            with closing(self._connect()) as conn:
                conflict = conn.execute(
                    "SELECT id FROM spans WHERE trace_id = ? AND sequence = ?",
                    (span.trace_id, span.sequence),
                ).fetchone()
            if conflict is not None and conflict[0] != span.id:
                raise DuplicateSequenceError(
                    f"Trace {span.trace_id} already contains sequence "
                    f"{span.sequence} on span {conflict[0]}."
                )

            _require_directory(path.parent, label="span shard", create=True)
            self._atomic_write(path, payload)
            try:
                with closing(self._connect()) as conn:
                    self._insert_index_row(conn, span)
                    conn.commit()
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            self._read_span(path, expected_id=span.id)
        return span.id

    def get(self, span_id: str) -> Span:
        path = self._span_path(span_id)
        if _lstat(path) is None:
            raise KeyError(span_id)
        return self._read_span(path, expected_id=span_id)

    def has(self, span_id: str) -> bool:
        path = self._span_path(span_id)
        if _lstat(path) is None:
            return False
        self._read_span(path, expected_id=span_id)
        return True

    def iter_spans(self, trace_id: str | None = None) -> Iterator[Span]:
        if trace_id is None:
            yield from self._iter_files()
            return
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id FROM spans WHERE trace_id = ? ORDER BY sequence, id",
                (trace_id,),
            ).fetchall()
        for (span_id,) in rows:
            yield self.get(span_id)

    def iter_traces(self) -> Iterator[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT trace_id FROM spans GROUP BY trace_id ORDER BY MIN(started_at), trace_id"
            ).fetchall()
        for (trace_id,) in rows:
            yield trace_id


__all__ = ["STORE_FORMAT", "STORE_VERSION", "Store"]
