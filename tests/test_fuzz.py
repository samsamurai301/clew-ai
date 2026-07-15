"""Property-based checks for the two untrusted portable input surfaces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from clew.core.bundle import verify_bundle
from clew.core.format import import_ndjson

_PUBLIC_KEY = Ed25519PrivateKey.generate().public_key()
_JSON_SCALARS = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(
        allow_nan=False,
        allow_infinity=False,
    )
    | st.text(max_size=80)
)
_JSON_VALUES = st.recursive(
    _JSON_SCALARS,
    lambda children: (
        st.lists(children, max_size=8) | st.dictionaries(st.text(max_size=30), children, max_size=8)
    ),
    max_leaves=30,
)


@pytest.mark.fuzz
@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)
@given(payload=st.binary(max_size=8192))
def test_bundle_verifier_rejects_arbitrary_bytes_without_escaping(
    tmp_path: Path,
    payload: bytes,
) -> None:
    bundle = tmp_path / "fuzz.tgz"
    bundle.write_bytes(payload)
    result = verify_bundle(bundle, _PUBLIC_KEY, max_total_bytes=16_384, max_members=100)
    assert result.valid is False
    assert result.span_files == []


@pytest.mark.fuzz
@settings(max_examples=200, deadline=None)
@given(value=_JSON_VALUES)
def test_ndjson_importer_handles_arbitrary_json_values(value: object) -> None:
    text = json.dumps(value, allow_nan=False) + "\n"
    try:
        trace_id, spans = import_ndjson(text, max_bytes=32_768, max_spans=100)
    except (OSError, OverflowError, TypeError, ValidationError, ValueError):
        return

    assert len(trace_id) == 32
    assert all(span.trace_id == trace_id for span in spans)
    assert len({span.id for span in spans}) == len(spans)
    assert len({span.sequence for span in spans}) == len(spans)
