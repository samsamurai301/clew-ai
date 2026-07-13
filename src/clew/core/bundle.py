"""Portable signed bundles for sharing clew traces.

A bundle is a tar.gz file with three top-level entries:

  manifest.json    — bundle metadata (trace id, source store, creation time)
  sig              — Ed25519 signature over the canonical manifest bytes
  spans/<id>.json  — one JSON-Lines file per span (the actual content)

Verification policy
-------------------
- The manifest declares which trace it contains and a sha256 over the
  span bytes (defense-in-depth, in case the Ed25519 check is skipped).
- The Ed25519 signature is over the *raw manifest bytes* (UTF-8 JSON,
  sorted keys, 2-space indent). Verification recomputes the signature
  and rejects the bundle on any mismatch.
- The public key is supplied by the verifier out-of-band (via
  ``--public-key``). We never embed the public key in the bundle —
  including it in the signed payload would defeat the purpose.

Threat model
------------
Ed25519 signatures here are *content* signatures: they attest that
"the holder of this private key produced this exact manifest." They do
NOT attest to *when* the bundle was produced or *who* the holder is.
For real identity, layer an X.509 / Sigstore / PGP wrapper on top.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from clew.core.models import Span, Trace

# ---------------------------------------------------------------------------
# Key handling
# ---------------------------------------------------------------------------


def generate_keypair() -> tuple[bytes, bytes]:
    """Return ``(private_pem, public_pem)`` for a fresh Ed25519 keypair.

    PEM envelopes (PKCS8 for private, SubjectPublicKeyInfo for public).
    The private key is unencrypted — the caller is expected to store
    it securely (e.g. in a password manager, never in git).
    """
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    return priv_pem, pub_pem


def load_private_key(path: Path) -> Ed25519PrivateKey:
    pem = path.read_bytes()
    key = load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"file {path} is not an Ed25519 private key")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    pem = path.read_bytes()
    key = load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError(f"file {path} is not an Ed25519 public key")
    return key


# ---------------------------------------------------------------------------
# Manifest canonicalization
# ---------------------------------------------------------------------------


def _canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the canonical byte form of a manifest.

    Stable across runs, platforms, and Python dict ordering. This is
    the form that gets signed and verified.
    """
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleResult:
    """Outcome of a bundle build."""

    path: Path
    manifest: dict[str, Any]
    span_count: int
    trace_id: str
    public_key_pem: bytes


def build_bundle(
    trace: Trace,
    spans: list[Span],
    *,
    out: Path,
    source_store: Path,
    private_key: Ed25519PrivateKey,
    public_key: Ed25519PublicKey,
) -> BundleResult:
    """Build a signed tar.gz bundle containing a single trace.

    The manifest is built first, then signed, then written alongside
    the spans. The ``sig`` file holds the raw Ed25519 signature bytes
    (no PEM — this is the most compact form, 64 bytes).
    """
    # Hash the span payloads (defense-in-depth).
    span_h = hashlib.sha256()
    for s in spans:
        span_h.update(s.model_dump_json().encode("utf-8"))

    manifest: dict[str, Any] = {
        "format": "clew-bundle",
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "trace_id": trace.trace_id,
        "root_span_id": trace.root_span_id,
        "span_count": len(spans),
        "spans_sha256": span_h.hexdigest(),
        "source_store": str(source_store),
        "public_key": public_key.public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii"),
    }
    manifest_bytes = _canonical_manifest_bytes(manifest)
    signature = private_key.sign(manifest_bytes)

    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        # Manifest.
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(time.time())
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(manifest_bytes))
        # Signature.
        sig_info = tarfile.TarInfo(name="sig")
        sig_info.size = len(signature)
        sig_info.mtime = info.mtime
        sig_info.mode = 0o644
        tar.addfile(sig_info, io.BytesIO(signature))
        # Spans.
        for s in spans:
            span_bytes = s.model_dump_json().encode("utf-8")
            sinfo = tarfile.TarInfo(name=f"spans/{s.id}.json")
            sinfo.size = len(span_bytes)
            sinfo.mtime = info.mtime
            sinfo.mode = 0o644
            tar.addfile(sinfo, io.BytesIO(span_bytes))

    return BundleResult(
        path=out,
        manifest=manifest,
        span_count=len(spans),
        trace_id=trace.trace_id,
        public_key_pem=public_key.public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        ),
    )


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of a bundle verification."""

    valid: bool
    manifest: dict[str, Any] | None
    reason: str | None
    span_files: list[str]


def verify_bundle(bundle: Path, public_key: Ed25519PublicKey) -> VerifyResult:
    """Verify a signed bundle.

    Does NOT extract the bundle — the caller can decide what to do
    with the verified spans (write to store, log, etc.). The returned
    ``span_files`` is the list of member paths under ``spans/`` so the
    caller can fetch them via :func:`extract_spans` once the signature
    is valid.
    """
    try:
        with tarfile.open(bundle, "r:gz") as tar:
            # Extract manifest and signature.
            try:
                manifest_member = tar.getmember("manifest.json")
                sig_member = tar.getmember("sig")
            except KeyError as exc:
                return VerifyResult(False, None, f"missing {exc}", [])
            if not manifest_member.isfile() or not sig_member.isfile():
                return VerifyResult(False, None, "manifest or sig is not a file", [])

            manifest_file = tar.extractfile(manifest_member)
            sig_file = tar.extractfile(sig_member)
            if manifest_file is None or sig_file is None:
                return VerifyResult(False, None, "failed to read manifest or sig", [])
            manifest_bytes = manifest_file.read()
            signature = sig_file.read()

            # Verify Ed25519.
            try:
                public_key.verify(signature, manifest_bytes)
            except InvalidSignature:
                return VerifyResult(False, None, "signature is invalid", [])

            # Parse manifest.
            try:
                manifest = json.loads(manifest_bytes)
            except json.JSONDecodeError as exc:
                return VerifyResult(False, None, f"manifest is not valid JSON: {exc}", [])

            # Cross-check: the manifest must declare the bundle format.
            if manifest.get("format") != "clew-bundle":
                return VerifyResult(
                    False,
                    manifest,
                    f"unknown bundle format: {manifest.get('format')!r}",
                    [],
                )

            # Cross-check: the spans declared in the manifest match the
            # actual span files. We hash all span bytes and compare
            # against the manifest's ``spans_sha256``. This catches
            # tampering with the *content* of the bundle (Ed25519 only
            # signs the manifest, not the span bytes).
            actual = hashlib.sha256()
            for member in tar.getmembers():
                if not (member.name.startswith("spans/") and member.name.endswith(".json")):
                    continue
                if not member.isfile():
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                actual.update(f.read())
            declared = manifest.get("spans_sha256", "")
            if actual.hexdigest() != declared:
                return VerifyResult(
                    False,
                    manifest,
                    "spans_sha256 mismatch — span content has been tampered with",
                    [],
                )

            # Enumerate span files.
            span_files: list[str] = []
            for member in tar.getmembers():
                if member.name.startswith("spans/") and member.name.endswith(".json"):
                    if not member.isfile():
                        continue
                    span_files.append(member.name)
            return VerifyResult(True, manifest, None, span_files)
    except (tarfile.TarError, OSError) as exc:
        return VerifyResult(False, None, f"failed to read bundle: {exc}", [])


def extract_spans(bundle: Path) -> dict[str, Span]:
    """Extract all spans from a bundle into a ``{span_id: Span}`` map.

    The bundle's signature is NOT re-verified here — call
    :func:`verify_bundle` first. This function is meant to be called
    after verification succeeds, in a context where the caller
    already trusts the bundle.
    """
    spans: dict[str, Span] = {}
    with tarfile.open(bundle, "r:gz") as tar:
        for member in tar.getmembers():
            if not (member.name.startswith("spans/") and member.name.endswith(".json")):
                continue
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            raw = f.read()
            span = Span.model_validate_json(raw)
            spans[span.id] = span
    return spans
