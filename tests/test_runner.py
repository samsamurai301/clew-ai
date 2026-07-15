"""Tests for clew.core.runner (subprocess tracing)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from clew.core.models import SpanStatus
from clew.core.runner import _last_nonempty_line, _tail, run_and_record
from clew.core.store import Store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _python_echo(msg: str) -> list[str]:
    return [sys.executable, "-c", f"print({msg!r})"]


def _python_stderr(msg: str) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.stderr.write({msg!r}); sys.exit(2)"]


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------


def test_tail_short_text_returned_unchanged() -> None:
    assert _tail("hello") == "hello"


def test_tail_long_text_truncated_with_marker() -> None:
    text = "a" * 10_000
    out = _tail(text, max_bytes=100)
    assert out.startswith("...\n")
    assert out.endswith("a" * 100)


def test_last_nonempty_line_ignores_blank_trailing() -> None:
    assert _last_nonempty_line("a\nb\n\n   \n") == "b"


def test_last_nonempty_line_returns_none_for_empty() -> None:
    assert _last_nonempty_line("") is None
    assert _last_nonempty_line("   \n\n  ") is None


# ---------------------------------------------------------------------------
# run_and_record
# ---------------------------------------------------------------------------


def test_run_and_record_successful_command(tmp_path: Path) -> None:
    """A zero-exit command produces an OK span with stdout's last line as output."""
    store = Store(tmp_path / ".clew")
    span = run_and_record(
        _python_echo("hello"),
        cwd=tmp_path,
        store=store,
    )
    assert span.status == SpanStatus.OK
    assert span.error is None
    assert span.output == "hello"
    assert span.attributes["returncode"] == 0
    assert "argv" in span.attributes


def test_run_and_record_failing_command(tmp_path: Path) -> None:
    """A non-zero exit produces an ERROR span with the error message."""
    store = Store(tmp_path / ".clew")
    span = run_and_record(
        _python_stderr("oops"),
        cwd=tmp_path,
        store=store,
    )
    assert span.status == SpanStatus.ERROR
    assert span.error is not None
    assert "exit 2" in span.error
    assert "oops" in span.error


def test_run_and_record_custom_name(tmp_path: Path) -> None:
    store = Store(tmp_path / ".clew")
    span = run_and_record(
        _python_echo("ok"),
        cwd=tmp_path,
        store=store,
        name="my-agent",
    )
    assert span.name == "my-agent"


def test_run_and_record_default_name_is_argv0(tmp_path: Path) -> None:
    store = Store(tmp_path / ".clew")
    span = run_and_record(_python_echo("x"), cwd=tmp_path, store=store)
    # argv[0] is the python executable; we use its basename.
    assert span.name == Path(sys.executable).name


def test_run_and_record_rejects_empty_argv(tmp_path: Path) -> None:
    store = Store(tmp_path / ".clew")
    with pytest.raises(ValueError, match="argv"):
        run_and_record([], cwd=tmp_path, store=store)


def test_run_and_record_timeout(tmp_path: Path) -> None:
    """A timeout produces an ERROR span with timeout info."""
    store = Store(tmp_path / ".clew")
    # Sleep 5 seconds; timeout after 0.1.
    argv = [sys.executable, "-c", "import time; time.sleep(5)"]
    span = run_and_record(argv, cwd=tmp_path, store=store, timeout_s=0.1)
    assert span.status == SpanStatus.ERROR
    assert span.error is not None
    assert "timeout" in span.error.lower()


def test_run_and_record_span_is_persisted(tmp_path: Path) -> None:
    """The span ends up in the store and is queryable by id."""
    store = Store(tmp_path / ".clew")
    span = run_and_record(
        _python_echo("persisted"),
        cwd=tmp_path,
        store=store,
    )
    # Re-open the store and look it up.
    store2 = Store(tmp_path / ".clew")
    got = store2.get(span.id)
    assert got.name == span.name
    assert got.output == "persisted"


def test_run_and_record_attributes_capture_argv(tmp_path: Path) -> None:
    """The full argv is preserved in attributes for replay."""
    store = Store(tmp_path / ".clew")
    argv = [sys.executable, "-c", "print('hi')"]
    span = run_and_record(argv, cwd=tmp_path, store=store)
    assert span.attributes["argv"] == argv
    assert span.attributes["cwd"] == str(tmp_path)
    assert "duration_s" in span.attributes


def test_run_and_record_allocates_unique_occurrence_ids(tmp_path: Path) -> None:
    """Every command occurrence remains distinct even for identical inputs."""
    store = Store(tmp_path / ".clew")
    argv = _python_echo("same")
    a = run_and_record(argv, cwd=tmp_path, store=store, name="run")
    b = run_and_record(argv, cwd=tmp_path, store=store, name="run")
    assert a.id != b.id
    assert a.trace_id != b.trace_id
    assert store.get(a.id).content_hash == a.content_hash
    assert store.get(b.id).content_hash == b.content_hash
