"""Public API for the clew SDK.

Importable surface:

    from clew.sdk import (
        Tracer,                # the main user-facing tracer
        SpanType, SpanStatus,  # span enums
        Span, Trace,           # data models
        current_span,          # context helpers
        current_trace_id,
        OTelBridge,            # OTel format converter
        instrument_openai,     # monkey-patch helpers
        instrument_anthropic,
    )
"""

from __future__ import annotations

from clew.core.models import Span, SpanStatus, SpanType, Trace
from clew.sdk.context import current_span, current_trace_id
from clew.sdk.otel import from_otel_span, instrument_anthropic, instrument_openai, to_otel_span
from clew.sdk.tracer import Tracer

# Re-export the OTelBridge alias (the actual conversion functions are
# imported above; OTelBridge is a thin facade for users who prefer
# the class-style API).
OTelBridge = type(
    "OTelBridge",
    (),
    {
        "to_otel_span": staticmethod(to_otel_span),
        "from_otel_span": staticmethod(from_otel_span),
    },
)

__all__ = [
    "OTelBridge",
    "Span",
    "SpanStatus",
    "SpanType",
    "Trace",
    "Tracer",
    "current_span",
    "current_trace_id",
    "from_otel_span",
    "instrument_anthropic",
    "instrument_openai",
    "to_otel_span",
]
