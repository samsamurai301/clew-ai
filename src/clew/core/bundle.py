"""Portable signed bundles for sharing clew traces.

A bundle is a tar.gz file with three top-level entries:

  manifest.json    — bundle metadata (trace id, creation time, member hashes)
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
  extraction: only ``manifest.json``, ``sig``, and ``spans/<32-hex>.json``
  are accepted. Hard-links, symlinks, device files, and any path
  containing ``..`` are refused.

Threat model
------------
Ed25519 signatures here are *content* signatures: they attest that
"the holder of this private key produced this exact manifest." They do
NOT attest to *when* the bundle was produced or *who* the holder is.
For real identity, layer an X.509 / Sigstore / PGP wrapper on top.

Bombs and resource limits
-------------------------
Bundles are expected to be small (a single trace is usually <10MB
even for deep trees). We enforce a 256MB cap on member payload bytes, bound the complete
decompressed tar stream (including PAX/GNU metadata) before parsing, and cap the tar at
100,000 members. Anyone receiving a bundle can override these with the ``max_total_bytes`` and
``max_members`` arguments to :func:`verify_bundle` /
:func:`extract_spans`.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import stat
import tarfile
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
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
from pydantic import ValidationError

from clew.core.models import Span, Trace
from clew.utils.hash import canonical_json, span_hash

# ---------------------------------------------------------------------------
# Limits and validation
# ---------------------------------------------------------------------------

#: Maximum decompressed size of a bundle (manifest + spans + sig).
#: 256MB is a generous cap; a single clew trace is rarely >10MB.
DEFAULT_MAX_BUNDLE_BYTES: int = 256 * 1024 * 1024

#: Maximum number of tar members. A single trace rarely has more
#: than a few hundred spans. A 100,000-member cap bounds metadata work
#: while leaving ample room for unusually large traces.
DEFAULT_MAX_MEMBERS: int = 100_000

#: Fixed and per-member allowance for tar headers, padding, and bounded
#: PAX/GNU extension metadata. The full decompressed tar stream is capped at
#: ``max_total_bytes + min(max_members * 1024, 64 MiB) + 1 MiB`` before the
#: tar parser sees it, so compressed metadata cannot bypass member limits.
_MAX_ARCHIVE_OVERHEAD_BYTES: int = 64 * 1024 * 1024
_FIXED_ARCHIVE_OVERHEAD_BYTES: int = 1024 * 1024
_SPOOL_MEMORY_BYTES: int = 8 * 1024 * 1024
_COPY_CHUNK_BYTES: int = 1024 * 1024

#: SHA-256 hex digest length. Span ids and content hashes are
#: lowercase hex characters (exactly 32 chars for occurrence identities).
_HEX32: re.Pattern[str] = re.compile(r"^[0-9a-f]{32}$")
BUNDLE_VERSION = 2

#: Allowed top-level bundle members.
_ALLOWED_MANIFEST = "manifest.json"
_ALLOWED_SIG = "sig"


def _validate_member_name(name: str) -> None:
    """Reject dangerous tar member names.

    The allowed forms are exactly:

    - ``manifest.json``
    - ``sig``
    - ``spans/<32-hex>.json``

    Anything else — absolute paths, ``..`` segments, hard/sym links,
    paths with NULs, control characters, or names that don't match
    the allowlist — is refused.
    """
    if not name or "\x00" in name or "\n" in name or "\r" in name:
        raise ValueError(f"invalid member name: {name!r}")
    if name.startswith(("/", "\\")):
        raise ValueError(f"absolute path in member name: {name!r}")
    # Reject any path component that is ``..`` or contains traversal.
    for part in name.replace("\\", "/").split("/"):
        if part in {"", ".", ".."}:
            raise ValueError(f"path traversal in member name: {name!r}")
    if name in (_ALLOWED_MANIFEST, _ALLOWED_SIG):
        return
    if name.startswith("spans/") and name.endswith(".json"):
        span_id = name[len("spans/") : -len(".json")]
        if not _HEX32.fullmatch(span_id):
            raise ValueError(f"invalid span id in member name: {name!r}")
        return
    raise ValueError(f"disallowed member name: {name!r}")


@contextmanager
def _open_bounded_tar(
    bundle: Path,
    *,
    max_total_bytes: int,
    max_members: int,
) -> Iterator[tarfile.TarFile]:
    """Open one regular gzip archive after bounding its full tar stream.

    Python's tar parser consumes PAX/GNU extension payloads inside
    :meth:`TarFile.next`, before it returns a :class:`TarInfo` to Clew. We
    therefore decompress into a bounded spooled file first. The tar parser
    never observes more bytes than the configured payload cap plus bounded
    header/padding metadata allowance.
    """
    if isinstance(max_total_bytes, bool) or max_total_bytes < 0:
        raise ValueError("max_total_bytes must be a non-negative integer")
    if isinstance(max_members, bool) or max_members < 1:
        raise ValueError("max_members must be a positive integer")
    overhead = min(max_members * 1024, _MAX_ARCHIVE_OVERHEAD_BYTES)
    archive_limit = max_total_bytes + overhead + _FIXED_ARCHIVE_OVERHEAD_BYTES

    before = bundle.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("bundle must be a regular single-link file")
    if before.st_size > archive_limit:
        raise ValueError(f"compressed bundle exceeds {archive_limit} bytes")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(bundle, flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ValueError("bundle changed while it was being opened")
        with (
            os.fdopen(fd, "rb") as source,
            gzip.GzipFile(fileobj=source, mode="rb") as decompressed,
            tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_BYTES) as spool,
        ):
            fd = -1
            total = 0
            while True:
                chunk = decompressed.read(min(_COPY_CHUNK_BYTES, archive_limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > archive_limit:
                    raise ValueError(f"bundle decompressed stream exceeds {archive_limit} bytes")
                spool.write(chunk)
            spool.seek(0)
            with tarfile.open(fileobj=spool, mode="r:") as tar:
                yield tar
    finally:
        if fd >= 0:
            os.close(fd)


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
    return canonical_json(manifest)


def _span_bytes(span: Span) -> bytes:
    """Return the same canonical record bytes used by the v2 store."""
    return canonical_json(span.model_dump(mode="json"))


def _update_span_digest(digest: Any, span_id: str, payload: bytes) -> None:
    """Bind both the member name and bytes into the bundle aggregate hash."""
    digest.update(span_id.encode("ascii"))
    digest.update(b"\0")
    digest.update(payload)
    digest.update(b"\0")


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
    ordered_spans = sorted(spans, key=lambda span: span.id)
    if {span.id for span in ordered_spans} != {span.id for span in trace.spans}:
        raise ValueError("bundle spans do not match the supplied trace")
    if any(span.trace_id != trace.trace_id for span in ordered_spans):
        raise ValueError("bundle contains a span from a different trace")
    if len({span.sequence for span in ordered_spans}) != len(ordered_spans):
        raise ValueError("bundle contains duplicate sequence values")
    span_h = hashlib.sha256()
    for span in ordered_spans:
        if not _HEX32.fullmatch(span.id):
            raise ValueError(f"refusing to bundle span with non-canonical id: {span.id!r}")
        if span_hash(span) != span.content_hash:
            raise ValueError(f"span {span.id} failed content hash verification")
        _update_span_digest(span_h, span.id, _span_bytes(span))

    manifest: dict[str, Any] = {
        "format": "clew-bundle",
        "version": BUNDLE_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "trace_id": trace.trace_id,
        "root_span_id": trace.root_span_id,
        "span_count": len(ordered_spans),
        "spans_sha256": span_h.hexdigest(),
        "source_store": source_store.name,
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
        for s in ordered_spans:
            # Refuse to bundle a span whose id is not a 32-char hex
            # string — such an id would write to ``spans/<garbage>/..``
            # on extraction, possibly escaping the extraction root.
            if not _HEX32.fullmatch(s.id):
                raise ValueError(f"refusing to bundle span with non-canonical id: {s.id!r}")
            span_bytes = _span_bytes(s)
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
        span_count=len(ordered_spans),
        trace_id=trace.trace_id,
        public_key_pem=public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo),
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
    verified_spans: tuple[Span, ...] = ()


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
    total = 0
    out: list[tarfile.TarInfo] = []
    names: set[str] = set()
    while True:
        m = tar.next()
        if m is None:
            break
        if len(out) >= max_members:
            raise ValueError(f"bundle has more than {max_members} members")
        _validate_member_name(m.name)
        if m.name in names:
            raise ValueError(f"bundle contains duplicate member name: {m.name!r}")
        names.add(m.name)
        if m.islnk() or m.issym() or m.ischr() or m.isblk() or m.isfifo() or m.isdev():
            raise ValueError(f"bundle contains disallowed member type: {m.name!r}")
        if m.isfile():
            total += m.size
            if total > max_total_bytes:
                raise ValueError(f"bundle uncompressed size exceeds {max_total_bytes} bytes")
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
    The exact parsed objects authenticated by this call are returned in
    ``verified_spans``. Import those objects directly; reopening the pathname
    with :func:`extract_spans` would not bind a later read to these bytes.
    """
    try:
        with _open_bounded_tar(
            bundle,
            max_total_bytes=max_total_bytes,
            max_members=max_members,
        ) as tar:
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
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
                return VerifyResult(False, None, f"manifest is not valid JSON: {exc}", [])

            # Cross-check: the manifest must declare the bundle format.
            if manifest.get("format") != "clew-bundle":
                return VerifyResult(
                    False,
                    manifest,
                    f"unknown bundle format: {manifest.get('format')!r}",
                    [],
                )
            if manifest.get("version") != BUNDLE_VERSION:
                return VerifyResult(
                    False,
                    manifest,
                    f"unsupported bundle version: {manifest.get('version')!r}; "
                    f"expected {BUNDLE_VERSION}",
                    [],
                )

            # Cross-check: the spans declared in the manifest match the
            # actual span files. We hash all span bytes and compare
            # against the manifest's ``spans_sha256``. This catches
            # tampering with the *content* of the bundle (Ed25519 only
            # signs the manifest, not the span bytes).
            actual = hashlib.sha256()
            parsed_spans: list[Span] = []
            for member in sorted(tar.getmembers(), key=lambda item: item.name):
                if not (member.name.startswith("spans/") and member.name.endswith(".json")):
                    continue
                if not member.isfile():
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                payload = f.read()
                span_id = Path(member.name).stem
                _update_span_digest(actual, span_id, payload)
                try:
                    span = Span.model_validate_json(payload)
                except (ValidationError, ValueError) as exc:
                    return VerifyResult(
                        False,
                        manifest,
                        f"span {span_id} failed record verification: {exc}",
                        [],
                    )
                if span.id != span_id:
                    return VerifyResult(
                        False,
                        manifest,
                        f"span member {member.name} contains id {span.id}",
                        [],
                    )
                parsed_spans.append(span)
            declared = manifest.get("spans_sha256", "")
            if actual.hexdigest() != declared:
                return VerifyResult(
                    False,
                    manifest,
                    "spans_sha256 mismatch — span content has been tampered with",
                    [],
                )

            if len(parsed_spans) != manifest.get("span_count"):
                return VerifyResult(False, manifest, "span_count mismatch", [])
            trace_id = manifest.get("trace_id")
            if any(span.trace_id != trace_id for span in parsed_spans):
                return VerifyResult(False, manifest, "bundle mixes trace ids", [])
            ids = {span.id for span in parsed_spans}
            if manifest.get("root_span_id") not in ids:
                return VerifyResult(False, manifest, "root span is missing", [])
            if len({span.sequence for span in parsed_spans}) != len(parsed_spans):
                return VerifyResult(False, manifest, "duplicate sequence values", [])
            for span in parsed_spans:
                missing = [parent for parent in span.parent_ids if parent not in ids]
                if missing:
                    return VerifyResult(
                        False,
                        manifest,
                        f"span {span.id} has missing parents {missing}",
                        [],
                    )
            roots = [span.id for span in parsed_spans if not span.parent_ids]
            if roots != [manifest.get("root_span_id")]:
                return VerifyResult(False, manifest, f"invalid root topology: {roots}", [])
            in_degree = {span.id: len(span.parent_ids) for span in parsed_spans}
            children: dict[str, list[str]] = {span.id: [] for span in parsed_spans}
            for span in parsed_spans:
                for parent_id in span.parent_ids:
                    children[parent_id].append(span.id)
            ready = [span_id for span_id, degree in in_degree.items() if degree == 0]
            resolved_count = 0
            while ready:
                span_id = ready.pop()
                resolved_count += 1
                for child_id in children[span_id]:
                    in_degree[child_id] -= 1
                    if in_degree[child_id] == 0:
                        ready.append(child_id)
            if resolved_count != len(parsed_spans):
                return VerifyResult(False, manifest, "trace contains a cycle", [])

            # Enumerate span files.
            span_files: list[str] = []
            for member in tar.getmembers():
                if (
                    member.name.startswith("spans/")
                    and member.name.endswith(".json")
                    and member.isfile()
                ):
                    span_files.append(member.name)
            return VerifyResult(True, manifest, None, span_files, tuple(parsed_spans))
    except (EOFError, OSError, tarfile.TarError, ValueError) as exc:
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
    with _open_bounded_tar(
        bundle,
        max_total_bytes=max_total_bytes,
        max_members=max_members,
    ) as tar:
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
    "DEFAULT_MAX_BUNDLE_BYTES",
    "DEFAULT_MAX_MEMBERS",
    "BundleResult",
    "VerifyResult",
    "build_bundle",
    "extract_spans",
    "generate_keypair",
    "load_private_key",
    "load_public_key",
    "verify_bundle",
]
