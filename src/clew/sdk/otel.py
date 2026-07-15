"""OTel-shaped projection and optional provider-client instrumentation."""

from __future__ import annotations

import functools
import inspect
from pathlib import Path
from typing import Any

from clew.core.format import from_otel, to_otel
from clew.core.models import Span, SpanType

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
}


def to_otel_span(span: Span) -> dict[str, Any]:
    """Convert a Clew span to the documented OTel-shaped dictionary."""
    return to_otel(span)


def from_otel_span(otel_dict: dict[str, Any]) -> Span:
    """Import one OTel-shaped dictionary with fresh Clew identities."""
    return from_otel(otel_dict)


def _jsonable(value: Any) -> Any:
    """Convert provider response objects to stable, persistable data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except (TypeError, ValueError):
            return _jsonable(model_dump())
    return str(value)


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None and key.lower().replace("-", "_") in _SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return _jsonable(value)


def _capture_input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    return {"args": _redact(list(args)), "kwargs": _redact(kwargs)}


def _capture_output(response: Any, *, provider: str) -> Any:
    """Return the assistant payload while retaining non-text responses."""
    if provider == "openai":
        choices = getattr(response, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if content is not None:
                return _jsonable(content)
    elif provider == "anthropic":
        content = getattr(response, "content", None)
        if content:
            text_blocks = [
                block.text
                for block in content
                if getattr(block, "type", None) == "text"
                and isinstance(getattr(block, "text", None), str)
            ]
            if text_blocks:
                return "".join(text_blocks)
    return _jsonable(response)


def _set_usage(span: Any, response: Any, *, provider: str) -> None:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return

    def read(*names: str) -> Any:
        for name in names:
            if isinstance(usage, dict) and name in usage:
                return usage[name]
            value = getattr(usage, name, None)
            if value is not None:
                return value
        return None

    input_tokens = read("input_tokens", "prompt_tokens")
    output_tokens = read("output_tokens", "completion_tokens")
    if input_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    if output_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    span.set_attribute("gen_ai.system", provider)


def _tracer_or_default(tracer: Any | None) -> Any:
    if tracer is not None:
        return tracer
    from clew.sdk.tracer import Tracer

    return Tracer(cwd=Path.cwd())


def _instrument_create(
    owner: Any,
    *,
    provider: str,
    operation_name: str,
    tracer: Any | None,
) -> None:
    original = getattr(owner, "create", None)
    if original is None or not callable(original):
        return
    if getattr(original, "__clew_wrapped__", False):
        existing_tracer = getattr(original, "__clew_tracer__", None)
        if tracer is None or tracer is existing_tracer:
            return
        raise ValueError(
            "provider client is already instrumented with a different Clew Tracer; "
            "create a separate client or reuse the original Tracer"
        )
    selected_tracer = _tracer_or_default(tracer)

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            with selected_tracer.trace(operation_name, type=SpanType.LLM) as span:
                span.set_input(_capture_input(args, kwargs))
                span.set_attribute("gen_ai.system", provider)
                if "model" in kwargs:
                    span.set_attribute("gen_ai.request.model", kwargs["model"])
                response = await original(*args, **kwargs)
                _set_usage(span, response, provider=provider)
                span.set_output(_capture_output(response, provider=provider))
                return response

        async_wrapped.__clew_wrapped__ = True  # type: ignore[attr-defined]
        async_wrapped.__clew_tracer__ = selected_tracer  # type: ignore[attr-defined]
        owner.create = async_wrapped
        return

    @functools.wraps(original)
    def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
        with selected_tracer.trace(operation_name, type=SpanType.LLM) as span:
            span.set_input(_capture_input(args, kwargs))
            span.set_attribute("gen_ai.system", provider)
            if "model" in kwargs:
                span.set_attribute("gen_ai.request.model", kwargs["model"])
            response = original(*args, **kwargs)
            _set_usage(span, response, provider=provider)
            span.set_output(_capture_output(response, provider=provider))
            return response

    sync_wrapped.__clew_wrapped__ = True  # type: ignore[attr-defined]
    sync_wrapped.__clew_tracer__ = selected_tracer  # type: ignore[attr-defined]
    owner.create = sync_wrapped


def instrument_openai(client: Any, tracer: Any | None = None) -> None:
    """Instrument sync or async OpenAI chat-completion calls idempotently."""
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    if completions is None:
        return
    _instrument_create(
        completions,
        provider="openai",
        operation_name="openai.chat.completions.create",
        tracer=tracer,
    )


def instrument_anthropic(client: Any, tracer: Any | None = None) -> None:
    """Instrument sync or async Anthropic message calls idempotently."""
    messages = getattr(client, "messages", None)
    if messages is None:
        return
    _instrument_create(
        messages,
        provider="anthropic",
        operation_name="anthropic.messages.create",
        tracer=tracer,
    )


__all__ = [
    "from_otel_span",
    "instrument_anthropic",
    "instrument_openai",
    "to_otel_span",
]
