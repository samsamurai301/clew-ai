"""Tests for clew.utils.hash — canonical JSON and SHA-256 content addressing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clew.core.models import Span, SpanStatus, SpanType
from clew.utils.hash import canonical_json, content_hash, span_hash

# ---------------------------------------------------------------------------
# canonical_json
# ---------------------------------------------------------------------------


def test_canonical_json_is_deterministic() -> None:
    """Same input → same output, byte-for-byte, every call."""
    obj = {"b": [1, 2, 3], "a": {"y": True, "x": None}}
    a = canonical_json(obj)
    b = canonical_json(obj)
    c = canonical_json(obj)
    assert a == b == c


def test_canonical_json_dict_key_order_independent() -> None:
    """Dict with same keys in different insertion orders produces the same bytes."""
    d1 = {"a": 1, "b": 2, "c": 3}
    d2 = {"c": 3, "a": 1, "b": 2}
    d3 = {"b": 2, "c": 3, "a": 1}
    assert canonical_json(d1) == canonical_json(d2) == canonical_json(d3)


def test_canonical_json_nested_key_order_independent() -> None:
    """Nested dicts are also order-independent."""
    d1 = {"outer": {"z": 1, "a": 2}, "x": [3, {"q": 4, "p": 5}]}
    d2 = {"x": [3, {"p": 5, "q": 4}], "outer": {"a": 2, "z": 1}}
    assert canonical_json(d1) == canonical_json(d2)


def test_canonical_json_array_order_preserved() -> None:
    """Array order is semantic and must be preserved (not sorted)."""
    a = canonical_json([1, 2, 3])
    b = canonical_json([3, 2, 1])
    assert a == b"[1,2,3]"
    assert b == b"[3,2,1]"
    assert a != b


def test_canonical_json_no_whitespace() -> None:
    """Output has no insignificant whitespace (no spaces, no newlines)."""
    out = canonical_json({"a": 1, "b": [1, 2]})
    assert b" " not in out
    assert b"\n" not in out
    assert b"\t" not in out


def test_canonical_json_returns_bytes() -> None:
    """canonical_json returns bytes, not str."""
    out = canonical_json({"a": 1})
    assert isinstance(out, bytes)


def test_canonical_json_utf8() -> None:
    """Non-ASCII characters are emitted as UTF-8, not escaped."""
    out = canonical_json({"emoji": "🦀", "korean": "안녕"})
    assert "🦀".encode() in out
    assert "안녕".encode() in out
    # No \uXXXX escapes for BMP-or-larger code points.
    assert b"\\u" not in out


def test_canonical_json_datetime_utc() -> None:
    """Datetimes are serialized in canonical form (Z suffix for UTC)."""
    dt = datetime(2026, 7, 13, 18, 28, 55, 123000, tzinfo=UTC)
    out = canonical_json({"t": dt})
    assert out == b'{"t":"2026-07-13T18:28:55.123000Z"}'


def test_canonical_json_rejects_nan() -> None:
    """NaN and Infinity are not valid JSON and must be rejected."""
    with pytest.raises(ValueError, match="[Nn]aN|[Ii]nf"):
        canonical_json({"x": float("nan")})
    with pytest.raises(ValueError, match="[Ii]nf"):
        canonical_json({"x": float("inf")})


def test_canonical_json_rejects_set() -> None:
    """Sets have no canonical ordering and are rejected outright."""
    with pytest.raises(TypeError, match="set|frozenset"):
        canonical_json({1, 2, 3})


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------


def test_content_hash_is_sha256_hex_64_chars() -> None:
    """content_hash returns 64 lowercase hex characters."""
    h = content_hash({"a": 1})
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_content_hash_is_known_value() -> None:
    """Hash of an empty-object canonical form is the well-known SHA-256."""
    # canonical_json({}) == b"{}" ; sha256("{}").hexdigest() is well-known.
    import hashlib

    assert content_hash({}) == hashlib.sha256(b"{}").hexdigest()


def test_content_hash_order_independent() -> None:
    """Hash of a dict is independent of key insertion order."""
    h1 = content_hash({"a": 1, "b": 2, "c": 3})
    h2 = content_hash({"c": 3, "b": 2, "a": 1})
    assert h1 == h2


def test_content_hash_changes_with_content() -> None:
    """Different content produces different hashes."""
    assert content_hash({"a": 1}) != content_hash({"a": 2})
    assert content_hash({"a": 1}) != content_hash({"b": 1})


def test_content_hash_nested() -> None:
    """Nested structures hash consistently across nesting orderings."""
    h1 = content_hash({"a": {"x": 1, "y": 2}, "b": [1, 2, 3]})
    h2 = content_hash({"b": [1, 2, 3], "a": {"y": 2, "x": 1}})
    assert h1 == h2


# ---------------------------------------------------------------------------
# span_hash
# ---------------------------------------------------------------------------


def _make_span(**overrides: object) -> Span:
    """Build a deterministic Span for hashing tests."""
    defaults: dict[str, object] = {
        "id": "a" * 64,
        "trace_id": "b" * 64,
        "parent_ids": [],
        "type": SpanType.LLM,
        "name": "test",
        "attributes": {"gen_ai.system": "openai", "gen_ai.request.model": "gpt-4o"},
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        "output": {"message": {"role": "assistant", "content": "hello"}},
        "started_at": datetime(2026, 7, 13, 18, 0, 0, tzinfo=UTC),
        "ended_at": datetime(2026, 7, 13, 18, 0, 1, tzinfo=UTC),
        "status": SpanStatus.OK,
    }
    defaults.update(overrides)
    return Span(**defaults)  # type: ignore[arg-type]


def test_span_hash_is_stable_for_same_input() -> None:
    """span_hash of the same span object is stable across repeated calls."""
    span = _make_span()
    h1 = span_hash(span)
    h2 = span_hash(span)
    h3 = span_hash(span)
    assert h1 == h2 == h3
    assert len(h1) == 64


def test_span_hash_is_stable_across_equal_instances() -> None:
    """Two spans with equal field values produce the same span_hash."""
    span1 = _make_span()
    span2 = _make_span()
    assert span1 == span2  # Pydantic frozen model equality
    assert span_hash(span1) == span_hash(span2)


def test_span_hash_changes_when_content_changes() -> None:
    """Different content fields produce different hashes."""
    base = _make_span()
    different_name = _make_span(name="other")
    different_input = _make_span(input={"messages": []})
    different_attrs = _make_span(attributes={"other": True})
    different_status = _make_span(status=SpanStatus.RUNNING)
    assert span_hash(base) != span_hash(different_name)
    assert span_hash(base) != span_hash(different_input)
    assert span_hash(base) != span_hash(different_attrs)
    assert span_hash(base) != span_hash(different_status)


def test_span_hash_excludes_trace_id() -> None:
    """Two spans identical except for trace_id produce the same hash."""
    span_a = _make_span(trace_id="a" * 64)
    span_b = _make_span(trace_id="c" * 64)
    assert span_a.trace_id != span_b.trace_id
    assert span_a.id == span_b.id
    assert span_hash(span_a) == span_hash(span_b)


def test_span_hash_includes_id() -> None:
    """Two spans with different ids but otherwise identical have different hashes."""
    span_a = _make_span(id="a" * 64)
    span_b = _make_span(id="b" * 64)
    assert span_hash(span_a) != span_hash(span_b)
