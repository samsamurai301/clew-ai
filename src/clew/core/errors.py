"""Typed failures exposed by Clew's persisted-format contracts."""

from __future__ import annotations


class ClewError(Exception):
    """Base class for actionable Clew failures."""


class StoreError(ClewError):
    """Base class for local-store failures."""


class UnsupportedStoreVersion(StoreError):  # noqa: N818 - public contract name
    """Raised when a store uses a format this release deliberately rejects."""


class StoreManifestError(StoreError):
    """Raised when a store manifest is missing or malformed."""


class SpanIntegrityError(StoreError):
    """Raised when persisted bytes do not match a span's integrity hash."""


class ConflictingSpanError(StoreError):
    """Raised when an occurrence id is reused with different bytes."""


class DuplicateSequenceError(StoreError):
    """Raised when two spans in one trace claim the same sequence number."""


class TraceTopologyError(StoreError):
    """Raised for missing parents, cycles, or other invalid trace topology."""


__all__ = [
    "ClewError",
    "ConflictingSpanError",
    "DuplicateSequenceError",
    "SpanIntegrityError",
    "StoreError",
    "StoreManifestError",
    "TraceTopologyError",
    "UnsupportedStoreVersion",
]
