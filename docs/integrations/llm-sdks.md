# OpenAI / Anthropic integration

clew ships with auto-instrumentation for the OpenAI and
Anthropic Python SDKs. The bridge wraps the client's
`create` method so every call writes a `clew` span.

```bash
uv add 'clew-ai[openai]'
# or
uv add 'clew-ai[anthropic]'
```

## Quick example

```python
import os
from openai import OpenAI
from clew.sdk import Tracer, SpanType
from clew.sdk.otel import instrument_openai

t = Tracer()
client = OpenAI()  # uses OPENAI_API_KEY from env
instrument_openai(client, tracer=t)

@t.agent
def research(question: str) -> str:
    @t.span("answer", type=SpanType.LLM)
    def answer() -> str:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": question}],
        )
        return resp.choices[0].message.content
    return answer()

research("what is clew?")
```

After the run, `clew show <trace_id>` reveals a tree where the
`answer` span (your explicit @t.span) is the parent of an
auto-instrumented `openai.chat.completions.create` span. The
inner span has `attributes.gen_ai.system=openai`,
`attributes.gen_ai.request.model=gpt-4o`, and the response
content as its `output`.

## Anthropic

```python
from anthropic import Anthropic
from clew.sdk.otel import instrument_anthropic

client = Anthropic()
instrument_anthropic(client, tracer=t)
```

The auto-instrumented span is named
`anthropic.messages.create`.

## What gets captured

For each call, the bridge records:

| Field | Source |
|---|---|
| `span.name` | `openai.chat.completions.create` or `anthropic.messages.create` |
| `span.type` | `SpanType.LLM` |
| `span.input` | positional arguments and redacted kwargs |
| `span.output` | the response's first message content |
| `span.attributes["gen_ai.system"]` | `"openai"` or `"anthropic"` |
| `span.attributes["gen_ai.request.model"]` | the model name |
| `span.attributes["gen_ai.usage.input_tokens"]` | `response.usage.prompt_tokens` |
| `span.attributes["gen_ai.usage.output_tokens"]` | `response.usage.completion_tokens` |
| `span.status` | `OK` on success, `ERROR` if the call raised |

## Idempotency

`instrument_openai(client)` and `instrument_anthropic(client)`
are safe to call multiple times. A second call on the same
client with the same tracer is a no-op; the existing wrap is preserved. Passing a
different explicit tracer for an already wrapped client raises `ValueError`, preventing
prompts and outputs from silently remaining pinned to the first tracer's store.

Both synchronous and asynchronous clients are supported. Nested calls inherit the active
Clew span through task-local context. Credential-shaped keys such as `api_key`, `token`,
`authorization`, and `password` are replaced with `[REDACTED]` before persistence.

## What it does NOT do

The bridge does **not** retry, cache, or rate-limit. It is a
purely observational layer. If you want retries, use the
upstream SDK's `with_retries` (or your own wrapper). If you
want cost tracking, sum
`gen_ai.usage.input_tokens + gen_ai.usage.output_tokens`
across the spans or export the data into the metrics system your application already uses.

## See also

- [OpenTelemetry integration](otel.md)
- [SDK reference](../reference/sdk.md)
