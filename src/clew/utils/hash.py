"""Canonical JSON serialization and SHA-256 record integrity.

This module is the single source of truth for how clew serializes values
to bytes for hashing and storage. Two implementations, two machines, two
Python versions must produce byte-for-byte identical output for the same
input. That property makes record verification portable across supported
Python versions and operating systems.

See :file:`PROTOCOL.md` §3 for the byte-level rules.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from clew.core.models import Span

#: Length of a SHA-256 hex digest (lowercase).
_HEX_LEN: Final[int] = 64


def _normalize(value: Any) -> Any:
    """Recursively coerce a value into a JSON-friendly, key-sorted form.

    * ``dict`` → dict with keys in lexicographic order of their UTF-8 bytes,
      values normalized recursively.
    * ``list`` / ``tuple`` → list, items normalized, **order preserved**
      (order is semantic for arrays per RFC 8785).
    * ``datetime`` → ISO 8601 with ``Z`` suffix for UTC, microsecond precision.
    * ``bytes`` / ``bytearray`` → decoded as UTF-8 (``isinstance`` check covers
      both). Binary blobs have no canonical form and are rejected with
      ``TypeError`` if they are not valid UTF-8.
    * Floats that are NaN, +Inf, or -Inf raise ``ValueError`` — JSON does
      not permit these and we refuse to silently produce undefined output.
    """
    if isinstance(value, Mapping):
        # JSON object keys are strings. Coercing (for example) ``1`` and
        # ``"1"`` would collapse distinct Python mappings and silently lose
        # data, so reject non-string keys before hashing or persistence.
        items: list[tuple[str, Any]] = []
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"Cannot canonicalize mapping key {k!r}: JSON object keys must be strings."
                )
            items.append((k, _normalize(v)))
        items.sort(key=lambda kv: kv[0].encode("utf-8"))
        return dict(items)
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, (set, frozenset)):
        # Sets have no canonical ordering; raise to avoid silent non-determinism.
        raise TypeError(
            "Cannot canonicalize set/frozenset (no stable ordering); "
            "convert to a sorted list before passing to canonical_json."
        )
    if isinstance(value, datetime):
        return _datetime_to_canonical(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8")
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(
                f"Cannot canonicalize non-finite float: {value!r}; JSON disallows NaN and Infinity."
            )
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    # Last resort: try to treat as a mapping (Pydantic models are mapping-like).
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        return _normalize(dumped)
    raise TypeError(
        f"Cannot canonicalize value of type {type(value).__name__!r}; "
        "supported: dict, list, tuple, str, int, float, bool, None, "
        "datetime, bytes, and Pydantic models."
    )


def _datetime_to_canonical(dt: datetime) -> str:
    """Format a datetime as RFC 3339 UTC with ``Z`` suffix."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    s = dt.astimezone(UTC).isoformat()
    # Pydantic/isoformat gives "+00:00" for UTC; we use the canonical "Z".
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s


def canonical_json(obj: Any) -> bytes:
    """Encode ``obj`` as canonical JSON (RFC 8785-style) and return UTF-8 bytes.

    Properties guaranteed by this encoder (see :file:`PROTOCOL.md` §3.1):

    1. Object keys are sorted lexicographically by their UTF-8 byte
       representation, recursively.
    2. No insignificant whitespace — only the minimum required separators
       (``,`` and ``:``).
    3. Arrays preserve order.
    4. NaN and Infinity are rejected.
    5. Strings use double quotes; control characters and ``"`` are
       escaped per RFC 8259.
    6. UTF-8 output (``ensure_ascii=False``).
    7. No trailing newline.

    Two calls with structurally equal inputs produce byte-equal output.
    """
    normalized = _normalize(obj)
    return json.dumps(
        normalized,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    ).encode("utf-8")


def content_hash(obj: Any) -> str:
    """Return the SHA-256 hex digest of the canonical-JSON encoding of ``obj``."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def span_hash(span: Span) -> str:
    """Return the exact-record integrity hash for a finalized span.

    Every persisted field participates except ``content_hash`` itself. The
    occurrence and trace identities are intentionally included: this digest
    verifies bytes and is never used for deduplication or identity.
    """
    return content_hash(span.model_dump(mode="python", exclude={"content_hash"}))


__all__ = ["canonical_json", "content_hash", "span_hash"]
