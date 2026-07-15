"""Path resolution helpers for the clew on-disk layout.

A clew repository is a single directory tree rooted at ``.clew/``. This
module locates that directory in two flavors:

* :func:`clew_root` — search the current working directory and up to
  five ancestors for an existing ``.clew/``; if none is found, return
  the expected path without creating it.
* :func:`global_clew_root` — the user-wide data directory for clew,
  typically ``~/.clew/`` on Linux and the platform-specific equivalent
  on macOS / Windows.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Final

import platformdirs

#: Maximum number of parent directories to search for an existing ``.clew/``.
#: ``5`` matches the spec; ``0`` is the start directory itself.
_MAX_PARENT_DEPTH: Final[int] = 5

#: Name of the per-project clew directory (sibling to ``.git/``).
_CLEW_DIRNAME: Final[str] = ".clew"

#: Application name passed to :func:`platformdirs.user_data_dir`.
_APP_NAME: Final[str] = "clew"


def clew_root(cwd: Path | None = None) -> Path:
    """Locate the ``.clew/`` directory for ``cwd`` without modifying disk.

    The search starts at ``cwd`` (or :func:`Path.cwd` if ``cwd`` is
    ``None``) and walks up to ``_MAX_PARENT_DEPTH + 1`` directories
    (the starting directory itself plus up to five ancestors). The
    first ``.clew/`` encountered is returned.

    If no ``.clew/`` is found in that range, the expected path under the
    original ``cwd`` is returned but not created. Initialization belongs
    exclusively to ``clew init`` or an explicit :class:`Store` creation.
    """
    start = (cwd or Path.cwd()).resolve()
    current = start
    for _ in range(_MAX_PARENT_DEPTH + 1):
        candidate = current / _CLEW_DIRNAME
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and stat.S_ISLNK(metadata.st_mode):
            raise ValueError(
                f"refusing symlinked Clew store at {candidate}; archive or remove the link "
                "and select a real .clew directory"
            )
        if metadata is not None and stat.S_ISDIR(metadata.st_mode):
            return candidate
        parent = current.parent
        if parent == current:
            # Reached the filesystem root without finding a .clew/.
            break
        current = parent
    return start / _CLEW_DIRNAME


def global_clew_root() -> Path:
    """Return the user-wide clew data directory, creating it if missing.

    Resolved via :func:`platformdirs.user_data_dir` with the app name
    ``"clew"`` — ``~/.local/share/clew/`` on Linux,
    ``~/Library/Application Support/clew/`` on macOS, and the
    platform-appropriate location on Windows. The directory and any
    missing parents are created.
    """
    base = Path(platformdirs.user_data_dir(_APP_NAME))
    base.mkdir(parents=True, exist_ok=True)
    return base


__all__ = ["clew_root", "global_clew_root"]
