"""Portable signed bundles for sharing clew traces.

A bundle is a tar.gz file with three top-level entries:

  manifest.json    — bundle metadata (trace id, source store, creation time)
  sig              — Ed25519 signature over the canonical manifest bytes
  spans/<id>.json  — one JSON file per span (the actual content)

Verification policy
-------------------
- The manifest declares which trace it contains and a sha256 over the
  span bytes (defense-in-depth, in case the Ed25519 check is skipped).
- The Ed25519 signature is over the *raw manifest bytes* (UTF-8 JSON,
  sorted keys, 2-space indent). Verification recomputes the signature
  and rejects the bundle on any mismatch.
- For convenience, the manifest also embeds the public key PEM.
  Verifiers SHOULD supply the public key out-of-band (via
  ``--public-key``); the embedded copy is a *hint* the verifier can
  compare against the expected key. If they differ, treat the bundle
  as suspicious.
- All tar members are validated against a strict allowlist before
  extraction: only ``manifest.json``, ``sig``, and ``spans/<8-64-hex>.json``
  are accepted. Hard-links, symlinks, device files, and any path
  containing ``..`` are refused. This blocks the
  `CVE-2025-4138` / `CVE-2025-4330` / `CVE-2025-4517` family of
  tarfile extraction filter bypasses.

Threat model
------------
Ed25519 signatures here are *content* signatures: they attest that
"the holder of this private key produced this exact manifest." They do
NOT attest to *when* the bundle was produced or *who* the holder is.
For real identity, layer an X.509 / Sigstore / PGP wrapper on top.

Bombs and resource limits
-------------------------
Bundles are expected to be small (a single trace is usually <10MB
even for deep trees). We enforce a 256MB cap on the *extracted* size
of a bundle, and a 1M-member cap on the tar. Anyone receiving a
bundle can override these with the ``max_total_bytes`` and
``max_members`` arguments to :func:`verify_bundle` /
:func:`extract_spans`.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
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
# Limits and validation
# ---------------------------------------------------------------------------

#: Maximum decompressed size of a bundle (manifest + spans + sig).
#: 256MB is a generous cap; a single clew trace is rarely >10MB.
DEFAULT_MAX_BUNDLE_BYTES: int = 256 * 1024 * 1024

#: Maximum number of tar members. A single trace rarely has more
#: than a few hundred spans; a million-member cap rejects bombs.
DEFAULT_MAX_MEMBERS: int = 1_000_000

#: SHA-256 hex digest length. Span ids and content hashes are
#: lowercase hex characters (8-64 chars).
_HEX64: re.Pattern[str] = re.compile(r"^[0-9a-f]{8,64}$")

#: Allowed top-level bundle members.
_ALLOWED_MANIFEST = "manifest.json"
_ALLOWED_SIG = "sig"


def _validate_member_name(name: str) -> None:
    """Reject dangerous tar member names.

    The allowed forms are exactly:

    - ``manifest.json``
    - ``sig``
    - ``spans/<8-64-hex>.json``

    Anything else — absolute paths, ``..`` segments, hard/sym links,
    paths with NULs, control characters, or names that don't match
    the allowlist — is refused.
    """
    if not name or "\x00" in name or "\n" in name or "\r" in name:
        raise ValueError(f"invalid member name: {name!r}")
    if name.startswith("/") or name.startswith("\\"):
        raise ValueError(f"absolute path in member name: {name!r}")
    # Reject any path component that is ``..`` or contains traversal.
    for part in name.replace("\\", "/").split("/"):
        if part in {"", ".", ".."}:
            raise ValueError(f"path traversal in member name: {name!r}")
    if name == _ALLOWED_MANIFEST or name == _ALLOWED_SIG:
        return
    if name.startswith("spans/") and name.endswith(".json"):
        span_id = name[len("spans/"):-len(".json")]
        if not _HEX64.match(span_id):
            raise ValueError(f"invalid span id in member name: {name!r}")
        return
    raise ValueError(f"disallowed member name: {name!r}")


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
    """Load an Ed25519 private key from a PEM file.

    Raises :class:`FileNotFoundError` if ``path`` is missing, and
    :class:`TypeError` (from ``cryptography``) if the file contains
    a non-Ed25519 key.
    """
    pem = path.read_bytes()
    key = load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"file {path} is not an Ed25519 private key")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load an Ed25519 public key from a PEM file.

    Raises :class:`FileNotFoundError` if ``path`` is missing, and
    :class:`ValueError` (from ``cryptography``) if the file contains
    a non-Ed25519 key.
    """
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
        # Public key is included for verifier convenience. Verifiers
        # SHOULD pass ``--public-key`` out-of-band; the embedded copy
        # is a hint that should be cross-checked.
        "public_key": public_key.public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii"),
    }
    manifest_bytes = _canonical_manifest_bytes(manifest)
    signature = private_key.sign(manifest_bytes)

    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        # Manifest.
        info = tarfile.TarInfo(name=_ALLOWED_MANIFEST)
        info.size = len(manifest_bytes)
        info.mtime = int(time.time())
        info.mode = 0o644
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        tar.addfile(info, io.BytesIO(manifest_bytes))
        # Signature.
        sig_info = tarfile.TarInfo(name=_ALLOWED_SIG)
        sig_info.size = len(signature)
        sig_info.mtime = info.mtime
        sig_info.mode = 0o644
        sig_info.uid = 0
        sig_info.gid = 0
        sig_info.uname = ""
        sig_info.gname = ""
        tar.addfile(sig_info, io.BytesIO(signature))
        # Spans.
        for s in spans:
            # Refuse to bundle a span whose id is not a 64-char hex
            # string — such an id would write to ``spans/<garbage>/..``
            # on extraction, possibly escaping the extraction root.
            if not _HEX64.match(s.id):
                raise ValueError(
                    f"refusing to bundle span with non-canonical id: {s.id!r}"
                )
            span_bytes = s.model_dump_json().encode("utf-8")
            sinfo = tarfile.TarInfo(name=f"spans/{s.id}.json")
            sinfo.size = len(span_bytes)
            sinfo.mtime = info.mtime
            sinfo.mode = 0o644
            sinfo.uid = 0
            sinfo.gid = 0
            sinfo.uname = ""
            sinfo.gname = ""
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


def _safe_list_members(
    tar: tarfile.TarFile,
    *,
    max_members: int,
    max_total_bytes: int,
) -> list[tarfile.TarInfo]:
    """Return the list of tar members after applying security filters.

    Rejects bundles that:
    - Contain hard links, symlinks, device files, FIFOs, or anything
      that isn't a regular file or directory.
    - Contain any member whose name is not on the allowlist (manifest,
      sig, or ``spans/<8-64-hex>.json``).
    - Contain more than ``max_members`` members.
    - Have a total uncompressed size exceeding ``max_total_bytes``.

    Use the *member-declared* size for the size cap, not the
    *extracted* size. This is a conservative upper bound — a malicious
    archive can claim a smaller size than it actually decompresses to,
    but the cap stops a bomb with a huge declared size before we
    even start extracting.
    """
    members = tar.getmembers()
    if len(members) > max_members:
        raise ValueError(
            f"bundle has {len(members)} members; max is {max_members}"
        )
    total = 0
    out: list[tarfile.TarInfo] = []
    for m in members:
        _validate_member_name(m.name)
        if m.islnk() or m.issym() or m.ischr() or m.isblk() or m.isfifo() or m.isdev():
            raise ValueError(
                f"bundle contains disallowed member type: {m.name!r}"
            )
        if m.isfile():
            total += m.size
            if total > max_total_bytes:
                raise ValueError(
                    f"bundle uncompressed size exceeds {max_total_bytes} bytes"
                )
        out.append(m)
    return out


def verify_bundle(
    bundle: Path,
    public_key: Ed25519PublicKey,
    *,
    max_total_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
    max_members: int = DEFAULT_MAX_MEMBERS,
) -> VerifyResult:
    """Verify a signed bundle.

    Does NOT extract the bundle to disk — the caller can decide what
    to do with the verified spans (write to store, log, etc.). The
    returned ``span_files`` is the list of member paths under
    ``spans/`` so the caller can fetch them via :func:`extract_spans`
    once the signature is valid.
    """
    try:
        with tarfile.open(bundle, "r:gz") as tar:
            # Apply security filters first — refuse bundles with
            # dangerous members (symlinks, traversal, bombs) before
            # doing any signature work.
            try:
                _safe_list_members(
                    tar,
                    max_members=max_members,
                    max_total_bytes=max_total_bytes,
                )
            except ValueError as exc:
                return VerifyResult(False, None, str(exc), [])

            # Extract manifest and signature.
            try:
                manifest_member = tar.getmember(_ALLOWED_MANIFEST)
                sig_member = tar.getmember(_ALLOWED_SIG)
            except KeyError as exc:
                return VerifyResult(False, None, f"missing {exc.args[0]!r}", [])
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
                if not (
                    member.name.startswith("spans/")
                    and member.name.endswith(".json")
                ):
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
                if (
                    member.name.startswith("spans/")
                    and member.name.endswith(".json")
                    and member.isfile()
                ):
                    span_files.append(member.name)
            return VerifyResult(True, manifest, None, span_files)
    except (tarfile.TarError, OSError) as exc:
        return VerifyResult(False, None, f"failed to read bundle: {exc}", [])


def extract_spans(
    bundle: Path,
    *,
    max_total_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
    max_members: int = DEFAULT_MAX_MEMBERS,
) -> dict[str, Span]:
    """Extract all spans from a bundle into a ``{span_id: Span}`` map.

    The bundle's signature is NOT re-verified here — call
    :func:`verify_bundle` first. This function is meant to be called
    after verification succeeds, in a context where the caller
    already trusts the bundle.

    The same security filters as :func:`verify_bundle` are applied
    to refuse dangerous member types and oversized bundles.
    """
    spans: dict[str, Span] = {}
    with tarfile.open(bundle, "r:gz") as tar:
        _safe_list_members(
            tar,
            max_members=max_members,
            max_total_bytes=max_total_bytes,
        )
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


__all__ = [
    "BundleResult",
    "DEFAULT_MAX_BUNDLE_BYTES",
    "DEFAULT_MAX_MEMBERS",
    "VerifyResult",
    "build_bundle",
    "extract_spans",
    "generate_keypair",
    "load_private_key",
    "load_public_key",
    "verify_bundle",
]
