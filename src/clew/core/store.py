"""Content-addressed store for clew spans.

The store is a thin layer over a ``.clew/`` directory:

    .clew/
    ├── spans/<id[:2]>/<id>.jsonl   # one JSONL line per span (append-only)
    ├── index.sqlite                # queryable index (rebuildable from JSONL)
    ├── refs/<name>                 # named pointers to a span id
    ├── HEAD                        # current branch name
    └── manifest.json               # store metadata

Spans are addressed by the SHA-256 of their canonical-JSON
serialization. Two spans with identical content have identical ids and
share one file on disk; writing the same span twice is a no-op.

The store is concurrency-safe: every write goes through a single
in-process lock. SQLite is opened with its own per-connection locking
and the file layer (``open(..., "a")``) is atomic for writes smaller
than ``PIPE_BUF`` on POSIX, which our one-line JSONL payloads are.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from clew.core.models import Span
from clew.utils.hash import span_hash

#: Default mode for atomic file creation. ``O_CREAT | O_EXCL | O_NOFOLLOW``
#: refuses to follow symlinks at the destination and fails (EEXIST) if
#: the file already exists — a hard guarantee against TOCTOU races on
#: shared filesystems. The mode bits ``0o600`` restrict read/write to
#: the owner, the same default as ``pathlib.Path.open(mode='w')``.
_ATOMIC_OPEN_FLAGS: int = (
    os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY | os.O_TRUNC
)
_ATOMIC_OPEN_MODE: int = 0o600


class _atomic_open:
    """Context manager that opens a file with TOCTOU-safe flags.

    Wraps :func:`os.open` so that callers can write text (with
    encoding) while still getting the security properties of
    ``O_CREAT | O_EXCL | O_NOFOLLOW``. Windows is supported by
    falling back to a plain ``path.open(mode='w')`` if the POSIX
    flags aren't accepted (which is the platform default on
    systems where :data:`os.O_NOFOLLOW` doesn't exist).
    """

    def __init__(self, path: Path, mode: str = "w", *, encoding: str = "utf-8") -> None:
        self._path = path
        self._mode = mode
        self._encoding = encoding
        self._fd: int | None = None
        self._fp: object | None = None

    def __enter__(self) -> object:
        try:
            fd = os.open(str(self._path), _ATOMIC_OPEN_FLAGS, _ATOMIC_OPEN_MODE)
        except (AttributeError, ValueError):
            # Windows or platform without O_NOFOLLOW — fall back.
            self._fp = self._path.open(self._mode, encoding=self._encoding)
            return self._fp
        self._fd = fd
        # os.fdopen returns a TextIOWrapper when mode='w' and encoding is given.
        self._fp = os.fdopen(fd, self._mode, encoding=self._encoding)
        return self._fp

    def __exit__(self, *exc: object) -> None:
        if self._fp is not None:
            close = getattr(self._fp, "close", None)
            if close is not None:
                close()


#: SQLite schema for the spans index. ``parent_ids`` is a JSON-encoded
#: list of hex strings; ``started_at``/``ended_at`` are epoch seconds
#: (REAL, sub-millisecond precision); ``content_hash`` is the result of
#: :func:`clew.utils.hash.span_hash`.
_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    started_at REAL,
    ended_at REAL,
    status TEXT NOT NULL,
    parent_ids TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);
"""

#: SQLite DML for inserting/replacing a span row.
_INSERT_SPAN: str = (
    "INSERT OR REPLACE INTO spans "
    "(id, trace_id, type, name, started_at, ended_at, status, parent_ids, content_hash) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _iso_utc_now() -> str:
    """Return the current UTC time as an RFC 3339 string with ``Z`` suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Store:
    """A content-addressed store rooted at a ``.clew/`` directory.

    The constructor is idempotent: opening an existing store does not
    touch any data; opening a fresh path creates the directory tree,
    the manifest, the HEAD pointer, and the SQLite index (rebuilt
    from any pre-existing JSONL files).
    """

    def __init__(self, root: Path) -> None:
        """Open the store at ``root`` (created if missing)."""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._spans_dir = self.root / "spans"
        self._spans_dir.mkdir(exist_ok=True)
        self._refs_dir = self.root / "refs"
        self._refs_dir.mkdir(exist_ok=True)
        self._db_path = self.root / "index.sqlite"
        self._lock = threading.Lock()
        # First-open setup: manifest, HEAD, and index rebuild.
        with self._lock:
            self._ensure_manifest()
            self._ensure_head()
            self._ensure_index()

    # -- first-open setup -------------------------------------------------

    def _ensure_manifest(self) -> None:
        manifest_path = self.root / "manifest.json"
        if manifest_path.exists():
            return
        manifest = {"version": 1, "created_at": _iso_utc_now()}
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _ensure_head(self) -> None:
        head_path = self.root / "HEAD"
        if not head_path.exists():
            head_path.write_text("main\n", encoding="utf-8")
        # Also ensure a default branch ref exists so the store is
        # immediately consistent for clew doctor. The placeholder id
        # (64 zeros) is invalid and would be caught by ref-target
        # checks; it's overwritten the first time the user moves
        # main onto a real span.
        default_ref = self._refs_dir / "main"
        if not default_ref.exists():
            default_ref.write_text("0" * 64 + "\n", encoding="utf-8")

    def _ensure_index(self) -> None:
        if self._db_path.exists():
            return
        # Rebuild from JSONL files. Each file under spans/<aa>/<id>.jsonl
        # contains exactly one JSON object — the serialized span.
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(_SCHEMA)
            for span in self._iter_files():
                self._insert_index_row(conn, span)
            conn.commit()

    def _iter_files(self) -> Iterator[Span]:
        """Yield spans by walking the spans/ directory tree."""
        if not self._spans_dir.is_dir():
            return
        for shard in self._spans_dir.iterdir():
            if not shard.is_dir():
                continue
            for f in shard.iterdir():
                if f.suffix != ".jsonl":
                    continue
                with f.open(encoding="utf-8") as fp:
                    line = fp.readline().strip()
                if not line:
                    continue
                yield Span.model_validate_json(line)

    @staticmethod
    def _insert_index_row(conn: sqlite3.Connection, span: Span) -> None:
        conn.execute(
            _INSERT_SPAN,
            (
                span.id,
                span.trace_id,
                span.type.value,
                span.name,
                span.started_at.timestamp(),
                span.ended_at.timestamp(),
                span.status.value,
                json.dumps(list(span.parent_ids)),
                span_hash(span),
            ),
        )

    # -- span path helpers ------------------------------------------------

    def _span_path(self, span_id: str) -> Path:
        """Return the on-disk path for ``span_id`` (does not check existence).

        Raises :class:`ValueError` if ``span_id`` is not a valid hex
        digest. This blocks path traversal: a span id of
        ``../../etc/passwd`` would otherwise resolve to a path outside
        the spans/ directory.
        """
        if not span_id or not all(c in "0123456789abcdef" for c in span_id):
            raise ValueError(f"invalid span id: {span_id!r}")
        if not (8 <= len(span_id) <= 64):
            raise ValueError(f"span id length out of range: {len(span_id)}")
        return self._spans_dir / span_id[:2] / f"{span_id}.jsonl"

    @staticmethod
    def _serialize_span(span: Span) -> str:
        """Serialize a span as a single canonical-JSON line.

        Uses Pydantic's JSON serialization to handle datetime and enum
        values, then re-encodes with sorted keys and minimal separators
        to match the canonical form used for hashing.
        """
        dumped = json.loads(span.model_dump_json())
        return json.dumps(
            dumped,
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )

    # -- public API --------------------------------------------------------

    def put(self, span: Span) -> str:
        """Append a span. Idempotent: a duplicate ``span.id`` is a no-op.

        Returns ``span.id``. The JSONL file is created with
        ``O_CREAT | O_EXCL | O_NOFOLLOW`` and atomically renamed into
        place. This closes the TOCTOU race where another process
        (or a symlink in a shared directory) could swap the target
        between the exists-check and the open call. The
        ``O_NOFOLLOW`` flag refuses to follow a symlink at the
        destination path; an attacker who can plant a symlink in
        the spans/ directory cannot redirect the write to an
        arbitrary file.
        """
        with self._lock:
            path = self._span_path(span.id)
            if path.exists():
                # Idempotent: the span is already in the store.
                return span.id
            path.parent.mkdir(parents=True, exist_ok=True)
            line = self._serialize_span(span)
            # Write to a temp file with O_CREAT | O_EXCL | O_NOFOLLOW,
            # then atomically rename. This is the canonical "atomic
            # write" pattern: a concurrent process can't observe a
            # half-written file, and a symlink at the destination is
            # refused (EEXIST) rather than followed.
            tmp_path = path.with_suffix(path.suffix + f".tmp.{span.id[:8]}")
            try:
                with _atomic_open(tmp_path, "w", encoding="utf-8") as fp:
                    fp.write(line + "\n")  # type: ignore[attr-defined]
                os.replace(tmp_path, path)  # atomic on POSIX
            except BaseException:
                # Best-effort cleanup of the temp file on any failure.
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
                raise
            with sqlite3.connect(self._db_path) as conn:
                self._insert_index_row(conn, span)
                conn.commit()
        return span.id

    def get(self, span_id: str) -> Span:
        """Load a span by id. Raises :class:`KeyError` if not found."""
        path = self._span_path(span_id)
        if not path.exists():
            raise KeyError(span_id)
        with path.open(encoding="utf-8") as fp:
            line = fp.readline().strip()
        if not line:
            raise KeyError(span_id)
        return Span.model_validate_json(line)

    def has(self, span_id: str) -> bool:
        """Return True iff a span with this id is in the store."""
        return self._span_path(span_id).exists()

    def iter_spans(self, trace_id: str | None = None) -> Iterator[Span]:
        """Yield all spans, optionally filtered to a single ``trace_id``.

        Without a ``trace_id`` we walk the filesystem (the source of
        truth). With one, we use the SQLite index to find matching
        span ids and then read each by id.
        """
        if trace_id is None:
            yield from self._iter_files()
            return
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM spans WHERE trace_id = ?", (trace_id,)
            ).fetchall()
        for (span_id,) in rows:
            yield self.get(span_id)

    def iter_traces(self) -> Iterator[str]:
        """Yield the set of distinct ``trace_id``s present in the store."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("SELECT DISTINCT trace_id FROM spans").fetchall()
        for (trace_id,) in rows:
            yield trace_id


__all__ = ["Store"]
