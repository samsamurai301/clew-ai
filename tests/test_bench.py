"""Tests for the ``clew bench`` command."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from clew.bench import bench


def test_bench_smoke() -> None:
    """Smoke test: bench returns a well-formed result dict."""
    with tempfile.TemporaryDirectory() as tmp:
        result = bench(
            Path(tmp) / ".clew",
            n_traces=5,
            spans_per_trace=20,
            n_orphans=10,
        )
    assert "record_ms" in result
    assert "diff_ms" in result
    assert "gc_ms" in result
    assert result["traces_recorded"] == 5
    assert result["spans_per_trace"] == 20
    assert result["n_orphans"] == 10
    assert all(k in result for k in ("diff_added", "diff_removed", "diff_changed"))


def test_bench_cli_runs() -> None:
    """``clew bench --spans 50 --traces 3 --orphans 10`` exits 0."""
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "clew",
                "bench",
                "--spans", "50",
                "--traces", "3",
                "--orphans", "10",
            ],
            capture_output=True,
            text=True,
            cwd=tmp,
            env={"PYTHONPATH": str(Path(__file__).parent.parent / "src")},
            timeout=60,
        )
    assert result.returncode == 0, result.stderr
    assert "record" in result.stdout
    assert "diff" in result.stdout
    assert "gc" in result.stdout


def test_bench_writes_json() -> None:
    """``clew bench --out <path>`` writes a JSON file."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bench.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "clew",
                "bench",
                "--spans", "30",
                "--traces", "2",
                "--orphans", "5",
                "--out", str(out),
            ],
            capture_output=True,
            text=True,
            cwd=tmp,
            env={"PYTHONPATH": str(Path(__file__).parent.parent / "src")},
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(out.read_text())
    assert "record_ms" in data
    assert data["traces_recorded"] == 2
