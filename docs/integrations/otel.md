# OpenTelemetry integration

clew is OpenTelemetry-compatible: traces flow in both
directions without lossy conversion. This page shows how.

## Why OTel?

The [OpenTelemetry](https://opentelemetry.io) GenAI semantic
conventions are the lingua franca of agent observability. Most
agent frameworks emit OTel-shaped spans out of the box. clew
honors those conventions so you can:

- Capture with OTel, import into clew for branching + replay.
- Capture with clew, export to OTel for an existing OTel
  pipeline.
- Run both: clew for local debugging, OTel for backend
  telemetry.

## Mapping

clew maps its `SpanType` to OTel's `kind`:

| clew `SpanType` | OTel `kind` | Notes |
|---|---|---|
| `LLM` | `SPAN_KIND_INTERNAL` | Attributes `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |
| `TOOL` | `SPAN_KIND_INTERNAL` | Attributes `gen_ai.tool.name`, `gen_ai.tool.call.id` |
| `DECISION` | `SPAN_KIND_INTERNAL` | No specific OTel attribute; clew-native |
| `OBSERVATION` | `SPAN_KIND_INTERNAL` | No specific OTel attribute; clew-native |

clew maps `SpanStatus` to OTel's status:

| clew `SpanStatus` | OTel status code |
|---|---|
| `OK` | `OK` |
| `ERROR` | `ERROR` (with message in `status.message`) |
| `RUNNING` | `UNSET` (not yet finalized) |

`started_at` and `ended_at` are RFC 3339 UTC with millisecond
precision.

## Round-trip

```bash
# Export a clew trace to OTel NDJSON
clew export <trace_id> --out trace.ndjson

# Import an OTel NDJSON (from any source) into clew
clew otel-import trace.ndjson --branch from-otel
```

Both directions preserve the full span content. The
`_kind: trace` header is a clew extension; bare OTel streams
(without the header) are also accepted — clew groups all
spans sharing a `trace_id` into one trace.

## Direct SDK bridge

The `clew.sdk.otel` module provides a thin layer that
auto-instruments popular LLM SDKs:

```python
from openai import OpenAI
from clew.sdk import Tracer
from clew.sdk.otel import instrument_openai

t = Tracer()
client = OpenAI()
instrument_openai(client, tracer=t)

# Now every call emits a clew LLM span.
client.chat.completions.create(model="gpt-4o", messages=[...])
```

The same exists for Anthropic:

```python
from anthropic import Anthropic
from clew.sdk.otel import instrument_anthropic

client = Anthropic()
instrument_anthropic(client, tracer=t)
```

The bridge is **idempotent**: instrumenting a client twice
does not double-wrap. The original method is preserved
(`.chat.completions.create` is replaced; the original lives
on the wrapped function as `__wrapped__`).

## When to use which

- **Use the SDK bridge** when you're writing new code and
  want zero-config tracing.
- **Use `clew export` / `otel-import`** when you have an
  existing OTel pipeline and want to feed traces in/out of
  clew without touching your code.
- **Use both** — the SDK bridge is non-invasive; the export
  pipeline is additive.

## See also

- [Architecture: format module](../internals/architecture.md#format)
- [SDK reference: `clew.sdk.otel`](../reference/sdk.md#clewsdkotel)
