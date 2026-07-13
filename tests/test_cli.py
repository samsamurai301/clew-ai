"""End-to-end tests for the clew CLI.

These tests invoke the actual ``clew`` binary (via ``typer.testing.CliRunner``)
against a temporary ``.clew`` store. They exercise the public command
surface and the JSON output paths.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clew.cli import app
from clew.core.models import Span, SpanStatus, SpanType

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
    assert "1.1" in result.stdout


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


def test_share_creates_signed_tarball(in_tmp: Path) -> None:
    """``clew share`` produces a signed tar.gz with manifest, sig, and span files."""
    runner.invoke(app, ["init", str(in_tmp)])
    # Seed a trace.
    import uuid as _uuid
    from datetime import datetime

    from clew.core.bundle import generate_keypair
    from clew.core.store import Store
    from clew.core.trace import TraceStore

    priv_pem, pub_pem = generate_keypair()
    key_path = in_tmp / "priv.pem"
    key_path.write_bytes(priv_pem)
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
    result = runner.invoke(
        app,
        [
            "share",
            trace_id,
            "--root",
            str(in_tmp / ".clew"),
            "--key",
            str(key_path),
            "--out",
            str(in_tmp / "out.tgz"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    out_path = in_tmp / "out.tgz"
    assert out_path.exists()
    # Inspect the tarball.
    import tarfile
    with tarfile.open(out_path, "r:gz") as tar:
        names = tar.getnames()
    assert "manifest.json" in names
    assert "sig" in names
    # Verify the signature with the matching public key.
    pub_path = in_tmp / "pub.pem"
    pub_path.write_bytes(pub_pem)
    v = runner.invoke(
        app, ["verify", str(out_path), "--public-key", str(pub_path)]
    )
    assert v.exit_code == 0, v.stdout


def test_keygen_creates_keypair(in_tmp: Path) -> None:
    """``clew keygen`` writes a private key + matching public key."""
    priv = in_tmp / "id"
    result = runner.invoke(app, ["keygen", "--out", str(priv)])
    assert result.exit_code == 0, result.stdout
    assert priv.exists()
    assert priv.with_suffix(priv.suffix + ".pub").exists()
    # Should be parseable as a keypair.
    from clew.core.bundle import load_private_key, load_public_key
    p = load_private_key(priv)
    pub = p.public_key()
    loaded = load_public_key(priv.with_suffix(priv.suffix + ".pub"))
    # Sign a test message to confirm round-trip.
    msg = b"hello"
    sig = p.sign(msg)
    loaded.verify(sig, msg)


def test_verify_rejects_tampered_bundle(in_tmp: Path) -> None:
    """A bundle with a tampered manifest is rejected by `clew verify`."""
    import io
    import json
    import tarfile
    import uuid as _uuid
    from datetime import datetime

    from clew.core.bundle import generate_keypair
    from clew.core.store import Store
    from clew.core.trace import TraceStore

    priv_pem, pub_pem = generate_keypair()
    (in_tmp / "priv.pem").write_bytes(priv_pem)
    (in_tmp / "pub.pem").write_bytes(pub_pem)

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
    runner.invoke(
        app,
        [
            "share",
            trace_id,
            "--root",
            str(in_tmp / ".clew"),
            "--key",
            str(in_tmp / "priv.pem"),
            "--out",
            str(in_tmp / "b.tgz"),
        ],
    )
    # Tamper with the manifest.
    tampered = in_tmp / "b_tampered.tgz"
    with tarfile.open(in_tmp / "b.tgz", "r:gz") as src, tarfile.open(
        tampered, "w:gz"
    ) as dst:
        for m in src.getmembers():
            if m.name == "manifest.json":
                f = src.extractfile(m)
                assert f is not None
                data = json.loads(f.read())
                data["trace_id"] = "TAMPERED"
                new_bytes = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
                ni = tarfile.TarInfo(name="manifest.json")
                ni.size = len(new_bytes)
                dst.addfile(ni, io.BytesIO(new_bytes))
            else:
                f = src.extractfile(m)
                if f is not None:
                    dst.addfile(m, io.BytesIO(f.read()))
    result = runner.invoke(
        app, ["verify", str(tampered), "--public-key", str(in_tmp / "pub.pem")]
    )
    assert result.exit_code == 1


def test_import_brings_spans_back(in_tmp: Path) -> None:
    """``clew import`` puts spans from a bundle into a fresh store."""
    import uuid as _uuid
    from datetime import datetime

    from clew.core.bundle import generate_keypair
    from clew.core.store import Store
    from clew.core.trace import TraceStore

    priv_pem, pub_pem = generate_keypair()
    (in_tmp / "priv.pem").write_bytes(priv_pem)
    (in_tmp / "pub.pem").write_bytes(pub_pem)

    # Source: one trace, signed + exported.
    src = in_tmp / "src"
    src.mkdir()
    runner.invoke(app, ["init", str(src)])
    sstore = Store(src / ".clew")
    sts = TraceStore(sstore)
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
    sts.add_span(s)
    bundle = in_tmp / "out.tgz"
    runner.invoke(
        app,
        [
            "share",
            trace_id,
            "--root",
            str(src / ".clew"),
            "--key",
            str(in_tmp / "priv.pem"),
            "--out",
            str(bundle),
        ],
    )
    # Destination: fresh store, import the bundle.
    dst = in_tmp / "dst"
    dst.mkdir()
    runner.invoke(app, ["init", str(dst)])
    result = runner.invoke(
        app,
        [
            "import",
            str(bundle),
            "--public-key",
            str(in_tmp / "pub.pem"),
            "--root",
            str(dst / ".clew"),
            "--branch",
            "shared",
        ],
    )
    assert result.exit_code == 0, result.stdout
    # The branch should exist.
    branches = runner.invoke(app, ["branches", "--root", str(dst / ".clew")])
    assert branches.exit_code == 0
    assert "shared" in branches.stdout


def test_replay_creates_new_trace(in_tmp: Path) -> None:
    """``clew replay <trace>`` returns a new trace id (different from original)."""
    import uuid as _uuid
    from datetime import datetime

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


# ---------------------------------------------------------------------------
# doctor / gc / query / export / otel-import / trace / show
# ---------------------------------------------------------------------------


def test_doctor_clean_store(in_tmp: Path) -> None:
    """Doctor on a fresh store: exit 0, healthy."""
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(app, ["doctor", "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 0
    assert "no issues found" in result.stdout.lower() or "ok" in result.stdout.lower()


def test_doctor_json_format(in_tmp: Path) -> None:
    """Doctor --json emits a parseable dict."""
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(
        app, ["doctor", "--json", "--root", str(in_tmp / ".clew")]
    )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "healthy" in parsed
    assert "errors" in parsed
    assert "warnings" in parsed
    assert parsed["healthy"] is True


def test_gc_dry_run_does_not_delete(in_tmp: Path) -> None:
    """clew gc --dry-run reports but doesn't touch the store."""
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(
        app,
        [
            "gc",
            "--dry-run",
            "--root",
            str(in_tmp / ".clew"),
            "--json",
        ],
    )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["scanned"] == 0
    assert parsed["deleted"] == 0


def test_query_lists_all_spans(in_tmp: Path) -> None:
    """query with no filters returns every span."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from clew.core.models import Span as _Span
    from clew.core.models import SpanStatus as _Status
    from clew.core.models import SpanType as _Type
    from clew.core.store import Store as _Store
    from clew.core.trace import TraceStore as _TS

    runner.invoke(app, ["init", str(in_tmp)])
    store = _Store(in_tmp / ".clew")
    ts = _TS(store)
    tid = _uuid.uuid4().hex
    for i in range(2):
        s = _Span(
            id=_uuid.uuid4().hex,
            trace_id=tid,
            parent_ids=[],
            type=_Type.OBSERVATION,
            name=f"step-{i}",
            attributes={},
            input="x",
            output="y",
            started_at=_dt.now(_UTC),
            ended_at=_dt.now(_UTC),
            status=_Status.OK,
        )
        ts.add_span(s)
    result = runner.invoke(app, ["query", "--root", str(in_tmp / ".clew")])
    assert result.exit_code == 0
    assert "step-0" in result.stdout
    assert "step-1" in result.stdout


def test_query_filter_by_name(in_tmp: Path) -> None:
    """query --name filters to matching spans only."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from clew.core.models import Span as _Span
    from clew.core.models import SpanStatus as _Status
    from clew.core.models import SpanType as _Type
    from clew.core.store import Store as _Store
    from clew.core.trace import TraceStore as _TS

    runner.invoke(app, ["init", str(in_tmp)])
    store = _Store(in_tmp / ".clew")
    ts = _TS(store)
    tid = _uuid.uuid4().hex
    for name in ("alpha", "beta", "alphabet"):
        ts.add_span(
            _Span(
                id=_uuid.uuid4().hex,
                trace_id=tid,
                parent_ids=[],
                type=_Type.OBSERVATION,
                name=name,
                attributes={},
                input="x",
                output="y",
                started_at=_dt.now(_UTC),
                ended_at=_dt.now(_UTC),
                status=_Status.OK,
            )
        )
    result = runner.invoke(
        app, ["query", "--name", "alpha", "--root", str(in_tmp / ".clew")]
    )
    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "alphabet" in result.stdout
    assert "beta" not in result.stdout


def test_query_filter_by_type_and_status(in_tmp: Path) -> None:
    """query --type and --status apply together."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from clew.core.models import Span as _Span
    from clew.core.models import SpanStatus as _Status
    from clew.core.models import SpanType as _Type
    from clew.core.store import Store as _Store
    from clew.core.trace import TraceStore as _TS

    runner.invoke(app, ["init", str(in_tmp)])
    store = _Store(in_tmp / ".clew")
    ts = _TS(store)
    tid = _uuid.uuid4().hex
    ts.add_span(
        _Span(
            id=_uuid.uuid4().hex,
            trace_id=tid,
            parent_ids=[],
            type=_Type.LLM,
            name="good",
            attributes={},
            input="x",
            output="y",
            started_at=_dt.now(_UTC),
            ended_at=_dt.now(_UTC),
            status=_Status.OK,
        )
    )
    bad = _Span(
        id=_uuid.uuid4().hex,
        trace_id=tid,
        parent_ids=[],
        type=_Type.TOOL,
        name="bad",
        attributes={},
        input="x",
        output="y",
        started_at=_dt.now(_UTC),
        ended_at=_dt.now(_UTC),
        status=_Status.ERROR,
        error="oops",
    )
    ts.add_span(bad)
    result = runner.invoke(
        app,
        [
            "query",
            "--type",
            "TOOL",
            "--status",
            "ERROR",
            "--root",
            str(in_tmp / ".clew"),
        ],
    )
    assert result.exit_code == 0
    assert "bad" in result.stdout
    assert "good" not in result.stdout


def test_query_rejects_unknown_type(in_tmp: Path) -> None:
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(
        app,
        ["query", "--type", "BOGUS", "--root", str(in_tmp / ".clew")],
    )
    assert result.exit_code == 1
    # _err prints to stderr via rich's stderr Console.
    output = (result.stdout + result.stderr).lower()
    assert "unknown type" in output


def test_query_rejects_bad_metadata_spec(in_tmp: Path) -> None:
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(
        app,
        ["query", "--metadata", "nokey", "--root", str(in_tmp / ".clew")],
    )
    assert result.exit_code == 1


def test_query_metadata_filter(in_tmp: Path) -> None:
    """query --metadata key=value works end-to-end."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from clew.core.models import Span as _Span
    from clew.core.models import SpanStatus as _Status
    from clew.core.models import SpanType as _Type
    from clew.core.store import Store as _Store
    from clew.core.trace import TraceStore as _TS

    runner.invoke(app, ["init", str(in_tmp)])
    store = _Store(in_tmp / ".clew")
    ts = _TS(store)
    tid = _uuid.uuid4().hex
    ts.add_span(
        _Span(
            id=_uuid.uuid4().hex,
            trace_id=tid,
            parent_ids=[],
            type=_Type.LLM,
            name="gpt4o-call",
            attributes={},
            input="x",
            output="y",
            started_at=_dt.now(_UTC),
            ended_at=_dt.now(_UTC),
            status=_Status.OK,
            metadata={"model": "gpt-4o"},
        )
    )
    ts.add_span(
        _Span(
            id=_uuid.uuid4().hex,
            trace_id=tid,
            parent_ids=[],
            type=_Type.LLM,
            name="claude-call",
            attributes={},
            input="x",
            output="y",
            started_at=_dt.now(_UTC),
            ended_at=_dt.now(_UTC),
            status=_Status.OK,
            metadata={"model": "claude-3"},
        )
    )
    result = runner.invoke(
        app,
        [
            "query",
            "--metadata",
            "model=gpt-4o",
            "--root",
            str(in_tmp / ".clew"),
        ],
    )
    assert result.exit_code == 0
    assert "gpt4o-call" in result.stdout
    assert "claude-call" not in result.stdout


def test_query_no_match_prints_no_matches(in_tmp: Path) -> None:
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(
        app, ["query", "--name", "nope", "--root", str(in_tmp / ".clew")]
    )
    assert result.exit_code == 0
    assert "no matches" in result.stdout.lower()


def test_query_json(in_tmp: Path) -> None:
    """query --json returns a parseable structure."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from clew.core.models import Span as _Span
    from clew.core.models import SpanStatus as _Status
    from clew.core.models import SpanType as _Type
    from clew.core.store import Store as _Store
    from clew.core.trace import TraceStore as _TS

    runner.invoke(app, ["init", str(in_tmp)])
    store = _Store(in_tmp / ".clew")
    ts = _TS(store)
    tid = _uuid.uuid4().hex
    ts.add_span(
        _Span(
            id=_uuid.uuid4().hex,
            trace_id=tid,
            parent_ids=[],
            type=_Type.OBSERVATION,
            name="x",
            attributes={},
            input="x",
            output="y",
            started_at=_dt.now(_UTC),
            ended_at=_dt.now(_UTC),
            status=_Status.OK,
        )
    )
    result = runner.invoke(
        app, ["query", "--json", "--root", str(in_tmp / ".clew")]
    )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["count"] == 1
    assert parsed["matches"][0]["name"] == "x"


def test_export_then_otel_import_round_trip(in_tmp: Path) -> None:
    """export -> otel-import brings the trace back into a fresh store."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from clew.core.models import Span as _Span
    from clew.core.models import SpanStatus as _Status
    from clew.core.models import SpanType as _Type
    from clew.core.store import Store as _Store
    from clew.core.trace import TraceStore as _TS

    # Source store with one trace.
    src = in_tmp / "src"
    src.mkdir()
    runner.invoke(app, ["init", str(src)])
    store = _Store(src / ".clew")
    ts = _TS(store)
    tid = _uuid.uuid4().hex
    s = _Span(
        id=_uuid.uuid4().hex,
        trace_id=tid,
        parent_ids=[],
        type=_Type.LLM,
        name="gpt-4o",
        attributes={"model": "gpt-4o"},
        input="hi",
        output="hello",
        started_at=_dt.now(_UTC),
        ended_at=_dt.now(_UTC),
        status=_Status.OK,
    )
    ts.add_span(s)
    ndjson_path = in_tmp / "trace.ndjson"
    export = runner.invoke(
        app, ["export", tid, "--out", str(ndjson_path), "--root", str(src / ".clew")]
    )
    assert export.exit_code == 0
    # Destination store.
    dst = in_tmp / "dst"
    dst.mkdir()
    runner.invoke(app, ["init", str(dst)])
    imp = runner.invoke(
        app,
        [
            "otel-import",
            str(ndjson_path),
            "--root",
            str(dst / ".clew"),
            "--branch",
            "imported",
        ],
    )
    assert imp.exit_code == 0, imp.stdout
    # Branch was created.
    br = runner.invoke(app, ["branches", "--root", str(dst / ".clew")])
    assert "imported" in br.stdout


def test_otel_import_rejects_bad_file(in_tmp: Path) -> None:
    """otel-import fails cleanly on a non-JSON file."""
    runner.invoke(app, ["init", str(in_tmp)])
    bad = in_tmp / "bad.ndjson"
    bad.write_text("not json at all")
    result = runner.invoke(
        app,
        ["otel-import", str(bad), "--root", str(in_tmp / ".clew")],
    )
    assert result.exit_code == 1


def test_otel_import_rejects_missing_file(in_tmp: Path) -> None:
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(
        app,
        [
            "otel-import",
            str(in_tmp / "does-not-exist.ndjson"),
            "--root",
            str(in_tmp / ".clew"),
        ],
    )
    assert result.exit_code == 1


def test_trace_records_subprocess(in_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`clew trace -- <cmd>` runs the command and writes a span."""
    runner.invoke(app, ["init", str(in_tmp)])
    # chdir so the subprocess is in in_tmp (the runner's cwd is the test's).
    monkeypatch.chdir(in_tmp)
    result = runner.invoke(
        app,
        [
            "trace",
            "--",
            sys.executable,
            "-c",
            "print('ok')",
            "--name",
            "subproc-test",
            "--root",
            str(in_tmp / ".clew"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    span_id = result.stdout.strip()
    assert len(span_id) == 64  # sha256 hex


def test_trace_rejects_empty_argv(in_tmp: Path) -> None:
    runner.invoke(app, ["init", str(in_tmp)])
    # typer exits 2 on missing required argument; that's a valid
    # failure for our purposes.
    result = runner.invoke(app, ["trace", "--root", str(in_tmp / ".clew")])
    assert result.exit_code != 0


def test_show_json(in_tmp: Path) -> None:
    """show --json emits one JSON object per line."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from clew.core.models import Span as _Span
    from clew.core.models import SpanStatus as _Status
    from clew.core.models import SpanType as _Type
    from clew.core.store import Store as _Store
    from clew.core.trace import TraceStore as _TS

    runner.invoke(app, ["init", str(in_tmp)])
    store = _Store(in_tmp / ".clew")
    ts = _TS(store)
    tid = _uuid.uuid4().hex
    ts.add_span(
        _Span(
            id=_uuid.uuid4().hex,
            trace_id=tid,
            parent_ids=[],
            type=_Type.OBSERVATION,
            name="root",
            attributes={},
            input="x",
            output="y",
            started_at=_dt.now(_UTC),
            ended_at=_dt.now(_UTC),
            status=_Status.OK,
        )
    )
    result = runner.invoke(
        app, ["show", tid, "--json", "--root", str(in_tmp / ".clew")]
    )
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("{")]
    assert lines
    parsed = json.loads(lines[0])
    assert parsed["name"] == "root"


def test_show_missing_trace_fails(in_tmp: Path) -> None:
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(
        app, ["show", "0" * 64, "--root", str(in_tmp / ".clew")]
    )
    assert result.exit_code == 1


def test_share_rejects_missing_trace(in_tmp: Path) -> None:
    """share prints an error and exits 1 for an unknown trace id."""
    from clew.core.bundle import generate_keypair

    priv, _ = generate_keypair()
    (in_tmp / "key.pem").write_bytes(priv)
    runner.invoke(app, ["init", str(in_tmp)])
    result = runner.invoke(
        app,
        [
            "share",
            "0" * 64,
            "--key",
            str(in_tmp / "key.pem"),
            "--root",
            str(in_tmp / ".clew"),
        ],
    )
    assert result.exit_code == 1
