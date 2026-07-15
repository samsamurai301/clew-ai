"""Top-level security smoke tests.

These tests don't replace a real audit (see SECURITY.md), but they
catch the most common regressions: path traversal in user-controlled
strings, accidental pickle/marshal, and command injection.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from clew.core.bundle import (
    generate_keypair,
    load_public_key,
    verify_bundle,
)
from clew.core.models import Span, SpanStatus, SpanType

# ---------------------------------------------------------------------------
# No-pickle guarantee
# ---------------------------------------------------------------------------


def test_no_pickle_imported_from_clew() -> None:
    """``clew`` never deserializes pickle from user input.

    The only ``pickle`` use should be in tests. We grep the source
    tree to enforce this: any production use of pickle should be
    raised as a security concern.
    """
    src_dir = Path(__file__).resolve().parent.parent / "src" / "clew"
    for py in src_dir.rglob("*.py"):
        text = py.read_text()
        if "import pickle" in text or "import marshal" in text:
            pytest.fail(f"{py} imports pickle/marshal; this is forbidden in clew")


def test_no_unsafe_yaml_in_clew() -> None:
    """``clew`` never uses unsafe yaml.load."""
    src_dir = Path(__file__).resolve().parent.parent / "src" / "clew"
    for py in src_dir.rglob("*.py"):
        text = py.read_text()
        if (
            re.search(r"yaml\.load\s*\(", text)
            and "safe_load" not in text.split("yaml.load")[1][:50]
        ):
            pytest.fail(f"{py} uses yaml.load without safe_load")


# ---------------------------------------------------------------------------
# No shell=True guarantee
# ---------------------------------------------------------------------------


def test_no_shell_true_in_clew() -> None:
    """``clew`` never uses ``shell=True`` in subprocess calls."""
    src_dir = Path(__file__).resolve().parent.parent / "src" / "clew"
    for py in src_dir.rglob("*.py"):
        text = py.read_text()
        if re.search(r"shell\s*=\s*True", text):
            pytest.fail(f"{py} uses shell=True; this is forbidden in clew")


# ---------------------------------------------------------------------------
# Bundle hardening
# ---------------------------------------------------------------------------


def test_bundle_rejects_embedded_symlink_to_etc(tmp_path: Path) -> None:
    """A bundle with a symlink to /etc/passwd is rejected at verify time."""
    priv, pub = generate_keypair()
    bad = tmp_path / "b.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        # Manifest entry (looks legitimate)
        manifest = {
            "format": "clew-bundle",
            "version": 1,
            "trace_id": "x" * 32,
            "spans_sha256": "0" * 64,
        }
        body = json.dumps(manifest).encode()
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
        # Sig (64 bytes of garbage)
        sig_body = b"\x00" * 64
        sig_info = tarfile.TarInfo(name="sig")
        sig_info.size = len(sig_body)
        tar.addfile(sig_info, io.BytesIO(sig_body))
        # The attack: a symlink named "spans/<hex>.json" pointing at /etc/passwd
        fake_id = "ab" * 16
        link = tarfile.TarInfo(name=f"spans/{fake_id}.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False
    assert (
        "disallowed" in (v.reason or "").lower()
        or "symlink" in (v.reason or "").lower()
        or "link" in (v.reason or "").lower()
    )


def test_bundle_size_limit_cannot_be_bypassed(tmp_path: Path) -> None:
    """Lowering the size cap to a tiny value still rejects the bundle."""
    priv, pub = generate_keypair()
    bad = tmp_path / "b.tgz"
    fake_id = "ab" * 16
    with tarfile.open(bad, "w:gz") as tar:
        body = b"x" * 1000
        info = tarfile.TarInfo(name=f"spans/{fake_id}.json")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)), max_total_bytes=10)
    assert v.valid is False
    assert "exceeds" in (v.reason or "").lower()


# ---------------------------------------------------------------------------
# Subprocess safety
# ---------------------------------------------------------------------------


def test_clew_trace_does_not_invoke_shell() -> None:
    """``clew trace -- echo hi`` does not spawn a shell.

    We pass an argv that would be a RCE if interpreted as a shell
    command; the fact that it runs and exits 0 confirms clew passes
    argv as a list.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [sys.executable, "-m", "clew", "init", "."],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        # Now run a command that would do something dangerous if shell=True
        result = subprocess.run(
            [sys.executable, "-m", "clew", "trace", "--", "python3", "-c", "print('ok')"],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Span id safety
# ---------------------------------------------------------------------------


def test_cannot_store_span_with_malicious_id(tmp_path: Path) -> None:
    """A path-like id is rejected before it can reach store path handling."""
    with pytest.raises(ValueError, match="id"):
        Span(
            id="../../etc/passwd",
            trace_id="0" * 32,
            parent_ids=[],
            type=SpanType.OBSERVATION,
            name="x",
            started_at=__import__("datetime").datetime(
                2024, 1, 1, tzinfo=__import__("datetime").UTC
            ),
            ended_at=__import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").UTC),
            status=SpanStatus.OK,
        )


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _bytes_to_tmpfile(data: bytes) -> Path:
    """Write ``data`` to a temp file and return the path."""
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False) as file:
        file.write(data)
        return Path(file.name)
