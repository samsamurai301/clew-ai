"""Tests for signed bundle build/verify/extract (clew.core.bundle)."""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from clew.core.bundle import (
    build_bundle,
    extract_spans,
    generate_keypair,
    load_private_key,
    load_public_key,
    verify_bundle,
)
from clew.core.models import Span, SpanStatus, SpanType, Trace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def keypair() -> tuple[bytes, bytes]:
    return generate_keypair()


@pytest.fixture
def priv_pem_path(tmp_path: Path, keypair: tuple[bytes, bytes]) -> Path:
    p = tmp_path / "priv.pem"
    p.write_bytes(keypair[0])
    return p


@pytest.fixture
def pub_pem_path(tmp_path: Path, keypair: tuple[bytes, bytes]) -> Path:
    p = tmp_path / "pub.pem"
    p.write_bytes(keypair[1])
    return p


@pytest.fixture
def priv(keypair: tuple[bytes, bytes]) -> Ed25519PrivateKey:
    return load_private_key(_bytes_to_tmpfile(keypair[0]))


@pytest.fixture
def pub(keypair: tuple[bytes, bytes]) -> Ed25519PublicKey:
    return load_public_key(_bytes_to_tmpfile(keypair[1]))


def _bytes_to_tmpfile(data: bytes) -> Path:
    p = Path(tempfile.mkstemp(suffix=".pem")[1])
    p.write_bytes(data)
    return p


def make_trace_with_spans(span_count: int = 3) -> tuple[Trace, list[Span]]:
    """Build a trace with ``span_count`` spans in a chain.

    Returns (Trace, list_of_spans). The first span is the root; each
    subsequent span has the previous as its sole parent.
    """
    trace_id = uuid4().hex
    spans: list[Span] = []
    for i in range(span_count):
        parent = [spans[-1].id] if spans else []
        s = Span(
            id=uuid4().hex,
            trace_id=trace_id,
            parent_ids=parent,
            type=SpanType.OBSERVATION,
            name=f"step-{i}",
            attributes={"i": i},
            input=f"in-{i}",
            output=f"out-{i}",
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
            ended_at=datetime(2024, 1, 2, tzinfo=UTC),
            status=SpanStatus.OK,
        )
        spans.append(s)
    trace = Trace(trace_id=trace_id, root_span_id=spans[0].id, spans=spans)
    return trace, spans


# ---------------------------------------------------------------------------
# Key handling
# ---------------------------------------------------------------------------


def test_generate_keypair_returns_pem() -> None:
    """Keypair generator returns PEM bytes for both halves."""
    priv, pub = generate_keypair()
    assert priv.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert pub.startswith(b"-----BEGIN PUBLIC KEY-----")


def test_load_private_key_rejects_non_ed25519(tmp_path: Path) -> None:
    """load_private_key raises on a non-Ed25519 / non-private PEM.

    We feed it a public-key PEM by mistake — that's the kind of
    bonehead move a user would actually make.
    """
    _, pub_pem = generate_keypair()
    p = tmp_path / "wrong.pem"
    p.write_bytes(pub_pem)
    with pytest.raises(ValueError, match="private key"):
        load_private_key(p)


def test_load_public_key_rejects_private_key(tmp_path: Path) -> None:
    priv_pem, _ = generate_keypair()
    p = tmp_path / "priv.pem"
    p.write_bytes(priv_pem)
    with pytest.raises(ValueError, match="public key"):
        load_public_key(p)


# ---------------------------------------------------------------------------
# Build + verify round-trip
# ---------------------------------------------------------------------------


def test_build_and_verify_round_trip(tmp_path: Path, keypair: tuple[bytes, bytes]) -> None:
    """Build a bundle, verify it with the matching public key, success."""
    priv, pub = keypair
    trace, spans = make_trace_with_spans(3)
    out = tmp_path / "bundle.clew.tgz"
    result = build_bundle(
        trace,
        spans,
        out=out,
        source_store=tmp_path / ".clew",
        private_key=load_private_key(_bytes_to_tmpfile(priv)),
        public_key=load_public_key(_bytes_to_tmpfile(pub)),
    )
    assert result.path == out
    assert result.span_count == 3
    assert result.trace_id == trace.trace_id
    assert out.exists()
    v = verify_bundle(out, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is True
    assert v.reason is None
    assert v.manifest is not None
    assert v.manifest["format"] == "clew-bundle"
    assert v.manifest["trace_id"] == trace.trace_id
    assert v.manifest["root_span_id"] == trace.root_span_id
    assert v.manifest["span_count"] == 3
    assert len(v.span_files) == 3


def test_verify_fails_on_wrong_key(tmp_path: Path) -> None:
    """A bundle signed with key A is rejected by key B."""
    priv_a, _ = generate_keypair()
    _, pub_b = generate_keypair()
    trace, spans = make_trace_with_spans(2)
    out = tmp_path / "b.tgz"
    build_bundle(
        trace,
        spans,
        out=out,
        source_store=tmp_path / ".clew",
        private_key=load_private_key(_bytes_to_tmpfile(priv_a)),
        public_key=load_private_key(_bytes_to_tmpfile(priv_a)).public_key(),
    )
    v = verify_bundle(out, load_public_key(_bytes_to_tmpfile(pub_b)))
    assert v.valid is False
    assert "signature" in (v.reason or "").lower()


def test_verify_fails_on_manifest_tamper(tmp_path: Path, keypair: tuple[bytes, bytes]) -> None:
    """Modifying the manifest breaks the Ed25519 signature."""
    priv, pub = keypair
    trace, spans = make_trace_with_spans(1)
    out = tmp_path / "b.tgz"
    build_bundle(
        trace,
        spans,
        out=out,
        source_store=tmp_path / ".clew",
        private_key=load_private_key(_bytes_to_tmpfile(priv)),
        public_key=load_public_key(_bytes_to_tmpfile(pub)),
    )
    # Re-pack with a tampered manifest.
    tampered = tmp_path / "b_tampered.tgz"
    with tarfile.open(out, "r:gz") as src, tarfile.open(tampered, "w:gz") as dst:
        for m in src.getmembers():
            if m.name == "manifest.json":
                data = json.loads(src.extractfile(m).read())  # type: ignore[arg-type]
                data["trace_id"] = "TAMPERED"
                new_bytes = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
                ni = tarfile.TarInfo(name="manifest.json")
                ni.size = len(new_bytes)
                dst.addfile(ni, io.BytesIO(new_bytes))
            else:
                f = src.extractfile(m)
                if f is not None:
                    dst.addfile(m, io.BytesIO(f.read()))
                else:
                    dst.add(m)
    v = verify_bundle(tampered, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False
    assert "signature" in (v.reason or "").lower()


def test_verify_fails_on_span_content_tamper(
    tmp_path: Path, keypair: tuple[bytes, bytes]
) -> None:
    """Modifying a span's content breaks the spans_sha256 cross-check."""
    priv, pub = keypair
    trace, spans = make_trace_with_spans(2)
    out = tmp_path / "b.tgz"
    build_bundle(
        trace,
        spans,
        out=out,
        source_store=tmp_path / ".clew",
        private_key=load_private_key(_bytes_to_tmpfile(priv)),
        public_key=load_public_key(_bytes_to_tmpfile(pub)),
    )
    tampered = tmp_path / "b_tampered.tgz"
    with tarfile.open(out, "r:gz") as src, tarfile.open(tampered, "w:gz") as dst:
        for m in src.getmembers():
            if m.name.startswith("spans/"):
                f = src.extractfile(m)
                if f is not None:
                    data = json.loads(f.read())
                    data["name"] = "TAMPERED"
                    new_bytes = json.dumps(data).encode("utf-8")
                    ni = tarfile.TarInfo(name=m.name)
                    ni.size = len(new_bytes)
                    dst.addfile(ni, io.BytesIO(new_bytes))
            else:
                f = src.extractfile(m)
                if f is not None:
                    dst.addfile(m, io.BytesIO(f.read()))
                else:
                    dst.add(m)
    v = verify_bundle(tampered, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False
    assert "sha256" in (v.reason or "").lower()


def test_verify_fails_on_missing_manifest(tmp_path: Path) -> None:
    """A tarball with no manifest.json is rejected.

    We test two variants: a tarball that contains only a
    disallowed member (rejected by the allowlist), and a tarball
    that is empty (rejected as "missing manifest").
    """
    priv, pub = generate_keypair()
    bad = tmp_path / "b.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name="README.md")
        body = b"hello"
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False
    # Either rejection reason is acceptable; the bundle must not pass.
    assert v.reason is not None

    # An empty tarball: also rejected as "missing".
    empty = tmp_path / "empty.tgz"
    with tarfile.open(empty, "w:gz") as tar:
        pass
    v2 = verify_bundle(empty, load_public_key(_bytes_to_tmpfile(pub)))
    assert v2.valid is False
    assert "missing" in (v2.reason or "").lower()


def test_verify_fails_on_corrupt_tar(tmp_path: Path) -> None:
    """A non-tarball file is rejected gracefully (no exception raised)."""
    priv, pub = generate_keypair()
    bad = tmp_path / "b.tgz"
    bad.write_bytes(b"this is not a tarball")
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False
    assert v.reason is not None


def test_verify_rejects_wrong_format(tmp_path: Path, keypair: tuple[bytes, bytes]) -> None:
    """A bundle with a non-clew format string is rejected.

    Construct by hand: write any file under spans/, sign a manifest
    that says format="other", and verify.
    """
    priv, pub = keypair
    out = tmp_path / "b.tgz"
    manifest = {
        "format": "other-tool-bundle",
        "version": 1,
        "trace_id": "x",
        "span_count": 0,
        "spans_sha256": "0" * 64,
        "created_at": "2024-01-01T00:00:00Z",
        "source_store": "/tmp",
        "public_key": pub.decode("ascii"),
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    signature = load_private_key(_bytes_to_tmpfile(priv)).sign(manifest_bytes)
    with tarfile.open(out, "w:gz") as tar:
        mi = tarfile.TarInfo(name="manifest.json")
        mi.size = len(manifest_bytes)
        tar.addfile(mi, io.BytesIO(manifest_bytes))
        si = tarfile.TarInfo(name="sig")
        si.size = len(signature)
        tar.addfile(si, io.BytesIO(signature))
    v = verify_bundle(out, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False
    assert "format" in (v.reason or "").lower()


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------


def test_extract_spans_round_trip(tmp_path: Path, keypair: tuple[bytes, bytes]) -> None:
    """extract_spans returns the original Span objects."""
    priv, pub = keypair
    trace, spans = make_trace_with_spans(3)
    out = tmp_path / "b.tgz"
    build_bundle(
        trace,
        spans,
        out=out,
        source_store=tmp_path / ".clew",
        private_key=load_private_key(_bytes_to_tmpfile(priv)),
        public_key=load_public_key(_bytes_to_tmpfile(pub)),
    )
    extracted = extract_spans(out)
    assert set(extracted.keys()) == {s.id for s in spans}
    for original in spans:
        roundtripped = extracted[original.id]
        assert roundtripped.id == original.id
        assert roundtripped.name == original.name
        assert roundtripped.parent_ids == original.parent_ids


# ---------------------------------------------------------------------------
# Manifest cross-checks
# ---------------------------------------------------------------------------


def test_manifest_includes_public_key(
    tmp_path: Path, keypair: tuple[bytes, bytes]
) -> None:
    """The manifest embeds the public key, so verifiers can match it.

    In practice, the public key is supplied out-of-band; this is a
    nice convenience for the CLI's `verify` flow when the signer
    included the key for you.
    """
    priv, pub = keypair
    trace, spans = make_trace_with_spans(1)
    out = tmp_path / "b.tgz"
    result = build_bundle(
        trace,
        spans,
        out=out,
        source_store=tmp_path / ".clew",
        private_key=load_private_key(_bytes_to_tmpfile(priv)),
        public_key=load_public_key(_bytes_to_tmpfile(pub)),
    )
    embedded = result.manifest["public_key"]
    assert embedded.startswith("-----BEGIN PUBLIC KEY-----")
    # And it's the same key.
    assert embedded.encode("ascii") == pub


def test_spans_sha256_cross_check_round_trip(
    tmp_path: Path, keypair: tuple[bytes, bytes]
) -> None:
    """The manifest's spans_sha256 matches the actual span bytes."""
    import hashlib

    priv, pub = keypair
    trace, spans = make_trace_with_spans(2)
    out = tmp_path / "b.tgz"
    result = build_bundle(
        trace,
        spans,
        out=out,
        source_store=tmp_path / ".clew",
        private_key=load_private_key(_bytes_to_tmpfile(priv)),
        public_key=load_public_key(_bytes_to_tmpfile(pub)),
    )
    h = hashlib.sha256()
    for s in spans:
        h.update(s.model_dump_json().encode("utf-8"))
    assert result.manifest["spans_sha256"] == h.hexdigest()


# ---------------------------------------------------------------------------
# Security hardening tests (CVE-2025-4138 / 4330 / 4517 / 7774 family)
# ---------------------------------------------------------------------------


def test_bundle_rejects_symlink_member(tmp_path: Path) -> None:
    """Bundles must not contain symlinks (CVE-2025-4330)."""
    bad = tmp_path / "b.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        # Create a symlink to /etc/passwd
        info = tarfile.TarInfo(name="evil")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    _, pub = generate_keypair()
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False
    assert "disallowed" in (v.reason or "").lower() or "link" in (v.reason or "").lower()


def test_bundle_rejects_path_traversal_member(tmp_path: Path) -> None:
    """Bundles must not contain members with .. in the path."""
    bad = tmp_path / "b.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name="spans/../../etc/passwd")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
    _, pub = generate_keypair()
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False


def test_bundle_rejects_absolute_path_member(tmp_path: Path) -> None:
    """Bundles must not contain absolute paths."""
    bad = tmp_path / "b.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
    _, pub = generate_keypair()
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False


def test_bundle_rejects_hardlink_member(tmp_path: Path) -> None:
    """Bundles must not contain hard links."""
    bad = tmp_path / "b.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name="manifest.json")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
        link = tarfile.TarInfo(name="hard")
        link.type = tarfile.LNKTYPE
        link.linkname = "manifest.json"
        tar.addfile(link)
    _, pub = generate_keypair()
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False


def test_bundle_rejects_oversized(tmp_path: Path) -> None:
    """Bundles that exceed the byte cap are rejected."""
    bad = tmp_path / "b.tgz"
    # Use a fake span id (32-char hex) with a large declared size
    fake_id = "ab" * 16
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name=f"spans/{fake_id}.json")
        body = b"x" * 200
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
        info2 = tarfile.TarInfo(name=f"spans/{'cd' * 16}.json")
        body2 = b"x" * 900
        info2.size = len(body2)
        tar.addfile(info2, io.BytesIO(body2))
    _, pub = generate_keypair()
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)), max_total_bytes=512)
    assert v.valid is False
    assert "exceeds" in (v.reason or "").lower()


def test_bundle_rejects_too_many_members(tmp_path: Path) -> None:
    """Bundles with more than max_members members are rejected."""
    bad = tmp_path / "b.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        for i in range(10):
            info = tarfile.TarInfo(name=f"f{i}")
            tar.addfile(info)
    _, pub = generate_keypair()
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)), max_members=5)
    assert v.valid is False
    assert "members" in (v.reason or "").lower()


def test_bundle_rejects_nul_in_member_name(tmp_path: Path) -> None:
    """Member names with NUL bytes are rejected."""
    bad = tmp_path / "b.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name="ok\x00.evil")
        info.size = 0
        tar.addfile(info, io.BytesIO(b""))
    _, pub = generate_keypair()
    v = verify_bundle(bad, load_public_key(_bytes_to_tmpfile(pub)))
    assert v.valid is False


def test_build_rejects_malformed_span_id(tmp_path: Path) -> None:
    """build_bundle refuses spans whose id contains path-traversal chars."""
    priv, pub = generate_keypair()
    bad_span = Span(
        id="../../etc/passwd",
        trace_id="0" * 32,
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name="x",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 1, tzinfo=UTC),
        status=SpanStatus.OK,
    )
    trace = Trace(
        trace_id="0" * 32,
        root_span_id="../../etc/passwd",
        spans=[bad_span],
    )
    with pytest.raises(ValueError, match="non-canonical"):
        build_bundle(
            trace,
            [bad_span],
            out=tmp_path / "b.tgz",
            source_store=tmp_path,
            private_key=load_private_key(_bytes_to_tmpfile(priv)),
            public_key=load_public_key(_bytes_to_tmpfile(pub)),
        )
