"""End-to-end test that exercises the full clew CLI on a real subprocess.

This is the test the release verifier runs. It invokes the
``clew`` binary (not the in-process runner) so it catches any
path/import issues the unit tests might miss.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore


def _seed_trace(tmp_path: Path) -> str:
    """Add a single span to a fresh .clew and return the trace id."""
    runner_root = tmp_path / ".clew"
    Store(runner_root).__init__  # ensure __init__ is fine
    store = Store(runner_root)
    ts = TraceStore(store)
    trace_id = uuid.uuid4().hex
    span = Span(
        id=uuid.uuid4().hex,
        trace_id=trace_id,
        parent_ids=[],
        type=SpanType.LLM,
        name="hello",
        attributes={"k": "v"},
        input={"q": "hi"},
        output={"a": "hello"},
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        status=SpanStatus.OK,
    )
    ts.add_span(span)
    return trace_id


def _run_clew(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke the clew binary as a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "clew", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_init_log_show_e2e(tmp_path: Path) -> None:
    """Init a store, seed a trace, then list + show it via the CLI."""
    proc = _run_clew("init", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / ".clew" / "manifest.json").exists()
    trace_id = _seed_trace(tmp_path)
    proc = _run_clew("log", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert trace_id[:12] in proc.stdout
    proc = _run_clew("show", trace_id, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "hello" in proc.stdout


def test_branch_and_checkout_e2e(tmp_path: Path) -> None:
    """Branch + checkout round-trip via the CLI."""
    _run_clew("init", cwd=tmp_path)
    trace_id = _seed_trace(tmp_path)
    # Find the root span.
    proc = _run_clew("show", trace_id, "--json", cwd=tmp_path)
    assert proc.returncode == 0
    span_id = json.loads(proc.stdout.splitlines()[0])["id"]
    proc = _run_clew("branch", "feature", span_id, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "Created branch" in proc.stdout
    proc = _run_clew("branches", cwd=tmp_path)
    assert "feature" in proc.stdout
    proc = _run_clew("checkout", "feature", cwd=tmp_path)
    assert proc.returncode == 0
    assert "Switched" in proc.stdout


def test_replay_and_diff_e2e(tmp_path: Path) -> None:
    """Replay then diff, both via the CLI."""
    _run_clew("init", cwd=tmp_path)
    trace_id = _seed_trace(tmp_path)
    proc = _run_clew("replay", trace_id, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    new_trace_id = proc.stdout.strip()
    assert new_trace_id != trace_id
    proc = _run_clew("diff", trace_id, new_trace_id, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "clew diff" in proc.stdout or "unchanged" in proc.stdout


def test_share_e2e(tmp_path: Path) -> None:
    """Share produces a valid signed tarball."""

    _run_clew("init", cwd=tmp_path)
    trace_id = _seed_trace(tmp_path)
    out = tmp_path / "out.tgz"
    # Generate a key for signing.
    from clew.core.bundle import generate_keypair
    priv_pem, _ = generate_keypair()
    key = tmp_path / "key.pem"
    key.write_bytes(priv_pem)
    proc = _run_clew(
        "share",
        trace_id,
        "--out",
        str(out),
        "--key",
        str(key),
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    import tarfile
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert "manifest.json" in names
    assert "sig" in names


def test_version_e2e() -> None:
    """The version flag prints the package version."""
    proc = _run_clew("version")
    assert proc.returncode == 0
    assert "1.1" in proc.stdout
