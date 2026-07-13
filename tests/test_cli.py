"""End-to-end tests for the clew CLI.

These tests invoke the actual ``clew`` binary (via ``typer.testing.CliRunner``)
against a temporary ``.clew`` store. They exercise the public command
surface and the JSON output paths.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clew.cli import app

runner = CliRunner()


@pytest.fixture
def in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Set cwd to a fresh tmp dir for the test."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_version() -> None:
    """``clew version`` prints the package version."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_help() -> None:
    """``clew --help`` lists the commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.stdout
    assert "log" in result.stdout
    assert "replay" in result.stdout


def test_init_creates_clew_dir(in_tmp: Path) -> None:
    """``clew init`` creates ``.clew/`` and a manifest.json."""
    result = runner.invoke(app, ["init", str(in_tmp)])
    assert result.exit_code == 0
    assert (in_tmp / ".clew").is_dir()
    assert (in_tmp / ".clew" / "manifest.json").is_file()


def test_init_is_idempotent(in_tmp: Path) -> None:
    """Running ``clew init`` twice doesn't error."""
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(app, ["init", str(in_tmp)])
    assert result.exit_code == 0


def test_log_empty(in_tmp: Path) -> None:
    """``clew log`` on an empty store shows a friendly message or empty table."""
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(app, ["log", "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 0
    # Empty store: either dim "No traces yet" message or empty table.
    assert "No traces" in result.stdout or "spans" in result.stdout


def test_log_json(in_tmp: Path) -> None:
    """``clew log --json`` emits parseable JSON lines."""
    runner.invoke(app, ["init", str(in_tmp)])
    # Seed a trace by adding a span directly via the SDK.
    import uuid
    from datetime import datetime

    from clew.core.models import Span, SpanStatus, SpanType
    from clew.core.store import Store
    from clew.core.trace import TraceStore

    store = Store(in_tmp / ".clew")
    ts = TraceStore(store)
    s = Span(
        id=uuid.uuid4().hex,
        trace_id=uuid.uuid4().hex,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="seed",
        attributes={},
        input="x",
        output="y",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        status=SpanStatus.OK,
    )
    ts.add_span(s)
    result = runner.invoke(app, ["log", "--root", str(in_tmp / ".clew"), "--json"])
    assert result.exit_code == 0
    lines = [l for l in result.stdout.splitlines() if l.startswith("{")]
    assert lines, "expected at least one JSON line"
    parsed = json.loads(lines[0])
    assert "trace_id" in parsed


def test_branch_create_and_list(in_tmp: Path) -> None:
    """Branch create + branches roundtrip."""
    import uuid as _uuid
    from datetime import datetime

    from clew.core.models import Span, SpanStatus, SpanType
    from clew.core.store import Store
    from clew.core.trace import TraceStore

    runner.invoke(app, ["init", str(in_tmp)])
    store = Store(in_tmp / ".clew")
    ts = TraceStore(store)
    span = Span(
        id=_uuid.uuid4().hex,
        trace_id=_uuid.uuid4().hex,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="root",
        attributes={},
        input="x",
        output="y",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        status=SpanStatus.OK,
    )
    ts.add_span(span)
    # Move main to point at our root, then create a new branch.
    from clew.core.branch import BranchManager
    bm = BranchManager(ts)
    bm.move("main", span.id)
    result = runner.invoke(app, ["branch", "feature-y", span.id, "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 0
    assert "Created branch" in result.stdout
    result = runner.invoke(app, ["branches", "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 0
    assert "feature-y" in result.stdout
    assert "main" in result.stdout


def test_branch_duplicate_fails(in_tmp: Path) -> None:
    """Creating a branch with an existing name fails with exit 1."""
    runner.invoke(app, ["init", str(in_tmp)])
    # main is auto-created; trying to create it again should fail.
    result = runner.invoke(app, ["branch", "main", "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 1


def test_checkout(in_tmp: Path) -> None:
    """``clew checkout <branch>`` switches HEAD."""
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(app, ["checkout", "main", "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 0
    assert "Switched" in result.stdout


def test_checkout_missing_fails(in_tmp: Path) -> None:
    """``clew checkout <missing>`` fails with exit 1."""
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(app, ["checkout", "nope", "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 1


def test_share_creates_tarball(in_tmp: Path) -> None:
    """``clew share`` produces a tar.gz with a manifest and the span files."""
    runner.invoke(app, ["init", str(in_tmp)])
    # Seed a trace.
    import uuid as _uuid
    from datetime import datetime

    from clew.core.models import Span, SpanStatus, SpanType
    from clew.core.store import Store
    from clew.core.trace import TraceStore

    store = Store(in_tmp / ".clew")
    ts = TraceStore(store)
    trace_id = _uuid.uuid4().hex
    s = Span(
        id=_uuid.uuid4().hex,
        trace_id=trace_id,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="root",
        attributes={},
        input="x",
        output="y",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        status=SpanStatus.OK,
    )
    ts.add_span(s)
    result = runner.invoke(app, ["share", trace_id, "--root", str(in_tmp / ".clew"), "--out", str(in_tmp / "out.tgz")])
    assert result.exit_code == 0
    out_path = in_tmp / "out.tgz"
    assert out_path.exists()
    # Inspect the tarball.
    import tarfile
    with tarfile.open(out_path, "r:gz") as tar:
        names = tar.getnames()
    assert "manifest.json" in names


def test_replay_creates_new_trace(in_tmp: Path) -> None:
    """``clew replay <trace>`` returns a new trace id (different from original)."""
    import uuid as _uuid
    from datetime import datetime

    from clew.core.models import Span, SpanStatus, SpanType
    from clew.core.store import Store
    from clew.core.trace import TraceStore

    runner.invoke(app, ["init", str(in_tmp)])
    store = Store(in_tmp / ".clew")
    ts = TraceStore(store)
    trace_id = _uuid.uuid4().hex
    s = Span(
        id=_uuid.uuid4().hex,
        trace_id=trace_id,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="root",
        attributes={},
        input="x",
        output="y",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        status=SpanStatus.OK,
    )
    ts.add_span(s)
    result = runner.invoke(app, ["replay", trace_id, "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 0
    new_id = result.stdout.strip()
    assert new_id != trace_id


def test_diff_between_two_traces(in_tmp: Path) -> None:
    """``clew diff <a> <b>`` shows the structural diff."""
    import uuid as _uuid
    from datetime import datetime

    from clew.core.models import Span, SpanStatus, SpanType
    from clew.core.store import Store
    from clew.core.trace import TraceStore

    runner.invoke(app, ["init", str(in_tmp)])
    store = Store(in_tmp / ".clew")
    ts = TraceStore(store)
    a_id = _uuid.uuid4().hex
    b_id = _uuid.uuid4().hex
    a = Span(
        id=_uuid.uuid4().hex,
        trace_id=a_id,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="a-root",
        attributes={},
        input="x",
        output="a-out",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        status=SpanStatus.OK,
    )
    b = Span(
        id=_uuid.uuid4().hex,
        trace_id=b_id,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="a-root",
        attributes={},
        input="x",
        output="b-out",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        status=SpanStatus.OK,
    )
    ts.add_span(a)
    ts.add_span(b)
    result = runner.invoke(app, ["diff", a_id, b_id, "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 0
    assert "modified" in result.stdout or "clew diff" in result.stdout
