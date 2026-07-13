"""OTel bridge: convert between clew spans and OpenTelemetry-style dicts.

This module does NOT depend on the ``opentelemetry`` package. It
implements just the format conversion (clew ``Span`` ↔ OTel ``gen_ai.*``
attribute dict) and the optional monkey-patch helpers for popular
LLM clients.

Why not depend on OTel SDK directly?

* clew is local-first and zero-dep is a virtue.
* Most users who want OTel interop already have the SDK installed;
  for them, the bridge is a small surface area.
* Users without OTel installed still get a working conversion
  (just dicts, no protocol).
"""

from __future__ import annotations

from typing import Any

from clew.core.format import from_otel, to_otel
from clew.core.models import Span

# These are imported lazily so the SDK works even if the user never
# instruments an OpenAI or Anthropic client. The patches are
# no-ops if the underlying library is missing.

_OTEL_ATTRIBUTES = {
    "system": "gen_ai.system",
    "model": "gen_ai.request.model",
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "finish_reason": "gen_ai.response.finish_reason",
    "completion": "gen_ai.completion",
    "tool_name": "gen_ai.tool.name",
    "tool_call_id": "gen_ai.tool.call.id",
}


def to_otel_span(span: Span) -> dict[str, Any]:
    """Convert a clew :class:`Span` to an OTel-style attribute dict."""
    return to_otel(span)


def from_otel_span(otel_dict: dict[str, Any]) -> Span:
    """Convert an OTel-style attribute dict to a clew :class:`Span`."""
    return from_otel(otel_dict)


def instrument_openai(client: Any) -> None:
    """Monkey-patch an OpenAI client to emit clew spans on every call.

    Wraps ``client.chat.completions.create`` so each call writes a
    span to a fresh ``.clew`` in the current working directory. The
    original method is preserved on the instance as ``__wrapped__``.

    No-op if the OpenAI library is not importable.
    """
    try:
        pass
    except Exception:
        return
    if not hasattr(client, "chat"):
        return
    original = client.chat.completions.create
    if getattr(original, "__clew_wrapped__", False):
        return

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        from pathlib import Path

        from clew.core.models import SpanType
        from clew.sdk.tracer import Tracer
        t = Tracer(cwd=Path.cwd())
        with t.trace("openai.chat.completions.create", type=SpanType.LLM) as span:
            span.set_attribute("gen_ai.system", "openai")
            if "model" in kwargs:
                span.set_attribute("gen_ai.request.model", kwargs["model"])
            span.set_output(None)
            response = original(*args, **kwargs)
            try:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", usage.prompt_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", usage.completion_tokens)
            except Exception:
                pass
            try:
                if getattr(response, "choices", None):
                    span.set_output(response.choices[0].message.content)
            except Exception:
                pass
            return response

    wrapped.__clew_wrapped__ = True  # type: ignore[attr-defined]
    client.chat.completions.create = wrapped  # type: ignore[assignment]  # type: ignore[assignment]


def instrument_anthropic(client: Any) -> None:
    """Monkey-patch an Anthropic client to emit clew spans on every call.

    Same shape as :func:`instrument_openai`. No-op if the anthropic
    SDK is not importable.
    """
    try:
        pass
    except Exception:
        return
    if not hasattr(client, "messages"):
        return
    original = client.messages.create
    if getattr(original, "__clew_wrapped__", False):
        return

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        from pathlib import Path

        from clew.core.models import SpanType
        from clew.sdk.tracer import Tracer
        t = Tracer(cwd=Path.cwd())
        with t.trace("anthropic.messages.create", type=SpanType.LLM) as span:
            span.set_attribute("gen_ai.system", "anthropic")
            if "model" in kwargs:
                span.set_attribute("gen_ai.request.model", kwargs["model"])
            response = original(*args, **kwargs)
            try:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
            except Exception:
                pass
            try:
                content = getattr(response, "content", None)
                if content and isinstance(content, list) and content:
                    span.set_output(content[0].text)
            except Exception:
                pass
            return response

    wrapped.__clew_wrapped__ = True  # type: ignore[attr-defined]
    client.messages.create = wrapped  # type: ignore[assignment]  # type: ignore[assignment]
