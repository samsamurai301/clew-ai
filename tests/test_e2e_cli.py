"""End-to-end tests that invoke the installed wheel via subprocess.

These tests do NOT run as part of the default test suite because they
require the wheel to be built and installed in a fresh venv. Run
them with:

    uv run --with pytest pytest tests/test_e2e_cli.py

or skip them when running the regular suite (they're marked with
``@pytest.mark.e2e``).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


def _venv_with_clew() -> Path:
    """Create a fresh venv with the current wheel + mcp installed.

    Returns the path to the venv's python binary.
    """
    venv = Path(tempfile.mkdtemp(prefix="clew_e2e_venv_"))
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        check=True, capture_output=True,
    )
    py = venv / "bin" / "python"
    # Install the wheel and the mcp extra
    wheel = Path(__file__).resolve().parent.parent / "dist" / "clew-1.1.3-py3-none-any.whl"
    if not wheel.exists():
        # Build it
        subprocess.run(["uv", "build"], cwd=wheel.parent.parent, check=True, capture_output=True)
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(wheel), "mcp"],
        check=True, capture_output=True,
    )
    return py


@pytest.fixture(scope="module")
def clew_python() -> Path:
    """A fresh venv with the current wheel installed."""
    return _venv_with_clew()


def _run(clew_python: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(clew_python.parent / "clew"), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _record(clew_python: Path, cwd: Path, prompt: str) -> str:
    """Record a trace via the SDK and return its trace_id."""
    code = (
        "import sys\n"
        f"sys.path.insert(0, r'{Path(__file__).resolve().parent.parent / 'src'}')\n"
        "from pathlib import Path\n"
        f"from clew.sdk import Tracer\n"
        f"t = Tracer(cwd=Path(r'{cwd}'))\n"
        "@t.agent\n"
        f"def demo(p): return 'ans-' + p\n"
        f"demo({prompt!r})\n"
    )
    r = subprocess.run(
        [str(clew_python), "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.e2e
def test_e2e_version(clew_python: Path) -> None:
    r = _run(clew_python, "--version")
    assert r.returncode == 0
    assert r.stdout.strip().startswith("clew ")


@pytest.mark.e2e
def test_e2e_init_record_log(clew_python: Path, tmp_path: Path) -> None:
    """The full init -> record -> log flow via subprocess."""
    assert _run(clew_python, "init", str(tmp_path)).returncode == 0
    _record(clew_python, tmp_path, "hi")
    r = _run(clew_python, "log", "--json", cwd=tmp_path)
    assert r.returncode == 0
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert len(lines) >= 1
    parsed = json.loads(lines[0])
    assert "trace_id" in parsed
    assert "root_name" in parsed


@pytest.mark.e2e
def test_e2e_show_and_html(clew_python: Path, tmp_path: Path) -> None:
    """``clew show`` and ``clew show --html`` both produce output."""
    _run(clew_python, "init", str(tmp_path))
    _record(clew_python, tmp_path, "x")
    r = _run(clew_python, "log", "--json", cwd=tmp_path)
    tid = json.loads(r.stdout.splitlines()[0])["trace_id"]

    # show
    r = _run(clew_python, "show", tid, cwd=tmp_path)
    assert r.returncode == 0
    assert "spans" in r.stdout

    # show --html
    html_path = tmp_path / "report.html"
    r = _run(clew_python, "show", tid, "--html", str(html_path), cwd=tmp_path)
    assert r.returncode == 0
    assert html_path.exists()
    text = html_path.read_text()
    assert "<!DOCTYPE html>" in text
    assert "clew trace" in text


@pytest.mark.e2e
def test_e2e_export_and_otel_import(clew_python: Path, tmp_path: Path) -> None:
    """``clew export`` -> ``clew otel-import`` roundtrips spans."""
    _run(clew_python, "init", str(tmp_path))
    _record(clew_python, tmp_path, "y")
    r = _run(clew_python, "log", "--json", cwd=tmp_path)
    tid = json.loads(r.stdout.splitlines()[0])["trace_id"]

    ndjson_path = tmp_path / "out.ndjson"
    r = _run(clew_python, "export", tid, "--out", str(ndjson_path), cwd=tmp_path)
    assert r.returncode == 0
    assert ndjson_path.exists()

    # Re-import into a fresh store
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _run(clew_python, "init", str(fresh))
    r = _run(clew_python, "otel-import", str(ndjson_path), cwd=fresh)
    assert r.returncode == 0
    assert "imported" in r.stdout


@pytest.mark.e2e
def test_e2e_keygen_share_verify(clew_python: Path, tmp_path: Path) -> None:
    """Bundle sign -> verify -> import roundtrip."""
    _run(clew_python, "init", str(tmp_path))
    _record(clew_python, tmp_path, "z")
    r = _run(clew_python, "log", "--json", cwd=tmp_path)
    tid = json.loads(r.stdout.splitlines()[0])["trace_id"]

    key = tmp_path / "k.pem"
    pub = tmp_path / "k.pub"
    assert _run(clew_python, "keygen", "--out", str(key), "--public-out", str(pub), cwd=tmp_path).returncode == 0
    # Private key is 0o600, public is 0o644
    if sys.platform != "win32":
        assert oct(key.stat().st_mode & 0o777) == "0o600"
        assert oct(pub.stat().st_mode & 0o777) == "0o644"

    bundle = tmp_path / "b.tgz"
    assert _run(clew_python, "share", tid, "--key", str(key), "--out", str(bundle), cwd=tmp_path).returncode == 0
    assert bundle.exists()

    r = _run(clew_python, "verify", str(bundle), "--public-key", str(pub))
    assert r.returncode == 0
    assert "valid" in r.stdout


@pytest.mark.e2e
def test_e2e_branch_checkout_replay_diff(clew_python: Path, tmp_path: Path) -> None:
    """Branch, checkout, replay, diff all work end-to-end."""
    _run(clew_python, "init", str(tmp_path))
    _record(clew_python, tmp_path, "a")
    r = _run(clew_python, "log", "--json", cwd=tmp_path)
    tid = json.loads(r.stdout.splitlines()[0])["trace_id"]

    assert _run(clew_python, "branch", "exp", "--root", tid, cwd=tmp_path).returncode == 0
    r = _run(clew_python, "branches", cwd=tmp_path)
    assert "exp" in r.stdout

    assert _run(clew_python, "checkout", "exp", cwd=tmp_path).returncode == 0

    r = _run(clew_python, "replay", tid, cwd=tmp_path)
    assert r.returncode == 0
    new_tid = r.stdout.strip()

    r = _run(clew_python, "diff", tid, new_tid, cwd=tmp_path)
    assert r.returncode == 0
    # The replay has the same content (mock executor), so diff is empty
    # but the command should succeed
    assert "added" in r.stdout or "modified" in r.stdout or "removed" in r.stdout


@pytest.mark.e2e
def test_e2e_doctor_gc_query(clew_python: Path, tmp_path: Path) -> None:
    """doctor, gc, query all work."""
    _run(clew_python, "init", str(tmp_path))
    _record(clew_python, tmp_path, "q")
    r = _run(clew_python, "doctor", cwd=tmp_path)
    assert r.returncode == 0
    assert "no issues found" in r.stdout or "healthy" in r.stdout

    r = _run(clew_python, "query", "--name", "demo", cwd=tmp_path)
    assert r.returncode == 0

    r = _run(clew_python, "gc", "--dry-run", cwd=tmp_path)
    assert r.returncode == 0


@pytest.mark.e2e
def test_e2e_trace_clean_env(clew_python: Path, tmp_path: Path) -> None:
    """``clew trace --clean-env`` strips parent env vars."""
    _run(clew_python, "init", str(tmp_path))
    env = os.environ.copy()
    env["CLEW_TEST_LEAKED_SECRET"] = "this-must-not-appear"
    # The subprocess prints its own env keys; if --clean-env is honored
    # CLEW_TEST_LEAKED_SECRET will not be in the env
    code = (
        "import os, sys\n"
        "secret = os.environ.get('CLEW_TEST_LEAKED_SECRET')\n"
        "if secret:\n"
        "    print(f'LEAK: {secret}')\n"
        "    sys.exit(1)\n"
        "else:\n"
        "    print('CLEAN')\n"
    )
    r = subprocess.run(
        [str(clew_python.parent / "clew"), "trace", "--clean-env", "--", "python3", "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True, text=True, timeout=30,
    )
    # The python3 -c output is in the span, not in stdout. Check the trace.
    assert r.returncode == 0, r.stderr
    log_r = _run(clew_python, "log", "--json", cwd=tmp_path)
    tid = json.loads(log_r.stdout.splitlines()[0])["trace_id"]
    # The stdout_tail appears in the printed tree; or we can query by name
    # show --json is NDJSON (one object per line); each line is a span.
    show_json_r = _run(clew_python, "show", tid, "--json", cwd=tmp_path)
    show_lines = [l for l in show_json_r.stdout.splitlines() if l.strip()]
    spans = [json.loads(l) for l in show_lines]
    attrs = spans[0].get("attributes", {}) if spans else {}
    stdout_tail = attrs.get("stdout_tail", "")
    assert "CLEAN" in stdout_tail, stdout_tail
    assert "LEAK" not in stdout_tail, stdout_tail
    assert "CLEW_TEST_LEAKED_SECRET" not in stdout_tail, stdout_tail
    stdout_tail = attrs.get("stdout_tail", "")
    assert "CLEAN" in stdout_tail
    assert "LEAK" not in stdout_tail
    assert "CLEW_TEST_LEAKED_SECRET" not in stdout_tail
    assert "hunter2" not in show_json_r.stdout, "actual value leaked:" + show_json_r.stdout


@pytest.mark.e2e
def test_e2e_mcp_stdio_roundtrip(clew_python: Path, tmp_path: Path) -> None:
    """Real stdio JSON-RPC: initialize, list tools, call list_traces."""
    _run(clew_python, "init", str(tmp_path))
    _record(clew_python, tmp_path, "mcp")

    # The MCP server's resources use cwd-relative path discovery.
    # chdir so the server finds the freshly-initialised store.
    proc = subprocess.Popen(
        [str(clew_python.parent / "clew"), "mcp"],
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True, bufsize=1,
    )
    try:
        def call(req, timeout=5):
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())

        r = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        }})
        assert r and r.get("result"), r
        assert r["result"]["serverInfo"]["name"] == "clew"

        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized"
        }) + "\n")
        proc.stdin.flush()

        r = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = r["result"]["tools"]
        assert len(tools) == 12

        r = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "list_traces", "arguments": {"root": str(tmp_path / ".clew")},
        }})
        parsed = json.loads(r["result"]["content"][0]["text"])
        assert len(parsed) >= 1

        r = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "doctor", "arguments": {"root": str(tmp_path / ".clew")},
        }})
        parsed = json.loads(r["result"]["content"][0]["text"])
        assert parsed["healthy"] is True

        r = call({"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {
            "uri": "store://info",
        }})
        parsed = json.loads(r["result"]["contents"][0]["text"])
        assert parsed["branches"] == ["main"]
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.e2e
def test_e2e_otel_roundtrip(clew_python: Path, tmp_path: Path) -> None:
    """``clew otel-import`` correctly ingests an OTel NDJSON file."""
    _run(clew_python, "init", str(tmp_path))
    sample = tmp_path / "sample.ndjson"
    sample.write_text(
        '{"_kind": "trace", "trace_id": "otel-test", "span_count": 2}\n'
        '{"_kind": "span", "name": "llm", "kind": "LLM", '
        '"start_time": "2024-01-01T00:00:00Z", "end_time": "2024-01-01T00:00:01Z", '
        '"trace_id": "otel-test", "span_id": "1111111111111111", '
        '"parent_span_id": "", "status": {"code": "OK"}, '
        '"attributes": {"gen_ai.system": "openai"}}\n'
        '{"_kind": "span", "name": "tool", "kind": "TOOL", '
        '"start_time": "2024-01-01T00:00:01Z", "end_time": "2024-01-01T00:00:02Z", '
        '"trace_id": "otel-test", "span_id": "2222222222222222", '
        '"parent_span_id": "1111111111111111", "status": {"code": "OK"}, '
        '"attributes": {"tool.name": "search"}}\n'
    )
    r = _run(clew_python, "otel-import", str(sample), "--branch", "otel", cwd=tmp_path)
    assert r.returncode == 0
    assert "imported 2/2" in r.stdout

    r = _run(clew_python, "show", "--json", "otel-test", cwd=tmp_path)
    lines = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    by_name = {s["name"]: s for s in lines}
    assert by_name["llm"]["type"] == "LLM"
    assert "gen_ai.system" in by_name["llm"]["attributes"]
    assert by_name["tool"]["type"] == "TOOL"


@pytest.mark.e2e
def test_e2e_bundle_tamper_detected(clew_python: Path, tmp_path: Path) -> None:
    """A bundle whose span content was modified fails verify."""
    _run(clew_python, "init", str(tmp_path))
    _record(clew_python, tmp_path, "tamper-test")
    r = _run(clew_python, "log", "--json", cwd=tmp_path)
    tid = json.loads(r.stdout.splitlines()[0])["trace_id"]

    key = tmp_path / "k.pem"
    pub = tmp_path / "k.pub"
    _run(clew_python, "keygen", "--out", str(key), "--public-out", str(pub), cwd=tmp_path)
    bundle = tmp_path / "b.tgz"
    _run(clew_python, "share", tid, "--key", str(key), "--out", str(bundle), cwd=tmp_path)

    # Tamper: change a span's content in the bundle
    import tarfile
    import io
    with tarfile.open(bundle, "r:gz") as src:
        members = []
        for m in src.getmembers():
            f = src.extractfile(m)
            content = f.read() if f is not None else b""
            if m.name.startswith("spans/"):
                content = b"TAMPERED" + content
                m.size = len(content)
            members.append((m, content))
    with tarfile.open(bundle, "w:gz") as dst:
        for m, content in members:
            dst.addfile(m, io.BytesIO(content))

    # Verify should now fail
    r = _run(clew_python, "verify", str(bundle), "--public-key", str(pub))
    assert r.returncode != 0
    assert "tamper" in r.stdout.lower() or "tamper" in r.stderr.lower()
