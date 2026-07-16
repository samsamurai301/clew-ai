"""Tests for safe project-store discovery."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clew.utils.paths import clew_root


def test_clew_root_finds_real_parent_store(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    child = tmp_path / "a" / "b"
    root.mkdir()
    child.mkdir(parents=True)
    assert clew_root(child) == root


def test_clew_root_does_not_create_missing_store(tmp_path: Path) -> None:
    expected = tmp_path / ".clew"
    assert clew_root(tmp_path) == expected
    assert not expected.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require Windows privileges")
def test_clew_root_rejects_symlinked_store(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    project = tmp_path / "project"
    outside.mkdir()
    project.mkdir()
    (project / ".clew").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinked Clew store"):
        clew_root(project)
