"""Git-style branch manager for clew reasoning traces.

A *branch* is a named pointer into the Merkle DAG of spans. Like git
refs, branches are stored as plain files on disk and updated atomically.
The ``HEAD`` file holds the name of the currently checked-out branch.

This module is the single source of truth for branch operations. Higher
layers (the SDK, the CLI) call into :class:`BranchManager` rather than
touching ``.clew/refs/*`` directly.

See :file:`ARCHITECTURE.md` § "Branching" for the rationale and
:file:`PROTOCOL.md` § "Refs" for the on-disk format.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from clew.core.models import Branch
from clew.core.trace import TraceStore

REFS_DIRNAME: str = "refs"
HEAD_FILENAME: str = "HEAD"
DEFAULT_BRANCH: str = "main"


class BranchManager:
    """Manage named branches (refs) and the current ``HEAD``.

    The manager wraps a :class:`TraceStore` so that branch operations
    are scoped to a single ``.clew/`` directory. Branches are durable
    files; the in-memory state is just a view over those files.

    Branches are stored as files at ``<root>/refs/<name>`` containing
    the head span id (one line, no trailing whitespace). ``HEAD`` is
    stored as ``<root>/HEAD`` containing the current branch name
    (one line).

    On first use, the manager ensures a default branch named
    :data:`DEFAULT_BRANCH` exists and points ``HEAD`` at it.
    """

    def __init__(self, store: TraceStore) -> None:
        """Attach to a :class:`TraceStore` and ensure defaults exist.

        Creates ``refs/`` and a default ``main`` branch if no refs
        exist yet. Sets ``HEAD`` to ``main`` if ``HEAD`` is missing.
        """
        self._store = store
        self._refs_dir = store.store.root / REFS_DIRNAME
        self._refs_dir.mkdir(parents=True, exist_ok=True)
        self._head_path = store.store.root / HEAD_FILENAME
        # Note: iterdir() returns a generator; `not iterdir()` is always
        # False. We must materialize with list() or any() to check
        # whether the directory is empty.
        if not any(self._refs_dir.iterdir()):
            # No refs exist: create a default branch file pointing at
            # an empty placeholder id. The user is expected to move
            # the branch to a real span with ``move()`` before
            # checking it out for meaningful work.
            placeholder = "0" * 64
            (self._refs_dir / DEFAULT_BRANCH).write_text(placeholder + "\n", encoding="utf-8")
        if not self._head_path.exists():
            self._head_path.write_text(DEFAULT_BRANCH + "\n", encoding="utf-8")

    # -- ref I/O ---------------------------------------------------------

    def _ref_path(self, name: str) -> Path:
        """Return the on-disk path for a named ref file.

        Raises :class:`ValueError` if ``name`` contains path
        separators or is otherwise unsafe.
        """
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
            raise ValueError(f"invalid branch name: {name!r}")
        return self._refs_dir / name

    def _read_ref(self, name: str) -> str:
        """Read a ref's head span id; raise :class:`KeyError` if missing."""
        path = self._ref_path(name)
        if not path.exists():
            raise KeyError(name)
        return path.read_text(encoding="utf-8").strip()

    def _write_ref(self, name: str, span_id: str) -> None:
        """Atomically write a ref's head span id."""
        path = self._ref_path(name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(span_id + "\n", encoding="utf-8")
        tmp.replace(path)

    # -- branch CRUD -----------------------------------------------------

    def create(self, name: str, head_span_id: str) -> Branch:
        """Create a new branch pointing at ``head_span_id``.

        Raises :class:`FileExistsError` if a branch with that name
        already exists. The new branch becomes the current ``HEAD``
        is NOT changed; use :meth:`checkout` to switch.
        """
        path = self._ref_path(name)
        if path.exists():
            raise FileExistsError(f"branch {name!r} already exists")
        self._write_ref(name, head_span_id)
        return Branch(name=name, head_span_id=head_span_id, created_at=datetime.now(UTC))

    def get(self, name: str) -> Branch:
        """Return the :class:`Branch` for ``name``; raise :class:`KeyError` if missing."""
        head_span_id = self._read_ref(name)
        return Branch(name=name, head_span_id=head_span_id, created_at=datetime.now(UTC))

    def list(self) -> list[Branch]:
        """Return all branches sorted by name."""
        names = sorted(p.name for p in self._refs_dir.iterdir() if p.is_file() and not p.name.endswith(".tmp"))
        return [
            Branch(name=n, head_span_id=self._read_ref(n), created_at=datetime.now(UTC))
            for n in names
        ]

    def delete(self, name: str) -> None:
        """Delete a branch. Raises :class:`KeyError` if missing.

        Refuses to delete the currently checked-out branch.
        """
        if name == self.current():
            raise ValueError(f"cannot delete the currently checked-out branch {name!r}")
        path = self._ref_path(name)
        if not path.exists():
            raise KeyError(name)
        path.unlink()

    def current(self) -> str:
        """Return the name of the currently checked-out branch."""
        return self._head_path.read_text(encoding="utf-8").strip()

    def checkout(self, name: str) -> None:
        """Switch ``HEAD`` to ``name``. Raises :class:`KeyError` if missing."""
        path = self._ref_path(name)
        if not path.exists():
            raise KeyError(name)
        self._head_path.write_text(name + "\n", encoding="utf-8")

    def move(self, name: str, new_head_span_id: str) -> Branch:
        """Move an existing branch to ``new_head_span_id``.

        Raises :class:`KeyError` if the branch does not exist.
        """
        if not self._ref_path(name).exists():
            raise KeyError(name)
        self._write_ref(name, new_head_span_id)
        return Branch(name=name, head_span_id=new_head_span_id, created_at=datetime.now(UTC))

    # -- convenience -----------------------------------------------------

    def head_span_id(self) -> str:
        """Return the span id the current branch points at."""
        return self._read_ref(self.current())

    def __repr__(self) -> str:
        """Compact string repr for debugging."""  # pragma: no cover - debug aid
        return f"BranchManager(refs_dir={self._refs_dir!s}, head={self.current()!r})"
