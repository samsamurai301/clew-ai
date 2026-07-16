# Python tracer

```python
from clew.sdk import SpanStatus, SpanType, Tracer
```

`@tracer.agent` starts one independently identified trace. `@tracer.span(...)` records a
nested sync, async, generator, or async-generator call. `tracer.trace(...)` records a manual
block.

```python
from pathlib import Path

from clew.sdk import SpanType, Tracer

tracer = Tracer(cwd=Path.cwd())

@tracer.agent
async def run(question: str) -> str:
    return await retrieve(question)

@tracer.span("retrieve", type=SpanType.TOOL)
async def retrieve(question: str) -> str:
    return f"result for {question}"
```

The agent root and every child have separate UUID4 occurrence IDs. The trace has its own
UUID4 `trace_id`. Parentage is task-local through `ContextVar`, and `sequence` gives each
occurrence deterministic order.

## Manual blocks

```python
with tracer.trace("database", type=SpanType.TOOL) as span:
    span.set_input({"query": "select ..."})
    result = run_query()
    span.set_attribute("db.system", "sqlite")
    span.set_output(result)
```

An exception or cancellation finalizes an `ERROR` span and is re-raised. Public `Span`
objects are immutable and terminal; in-flight mutable builders are internal.

## Replay SDK

```python
from clew.core.replay import ReplayEngine, RecordingExecutor
from clew.sdk import ReplayContext, ReplayResult, Span

async def execute(span: Span, context: ReplayContext) -> ReplayResult:
    return ReplayResult(output={"replayed": span.name})

engine = ReplayEngine(tracer.store, executor=RecordingExecutor(execute))
new_trace = await engine.replay(source_trace_id, from_span_id=source_span_id)
```

The executor supplies payload changes only. The engine owns destination identity, parents,
sequence, timestamps, terminal status, and integrity hash.

## Provider wrappers

`instrument_openai` and `instrument_anthropic` support sync and async clients and retain
the currently active Clew parent. Install their named extras before importing the provider
SDK. Repeated instrumentation is idempotent for the same tracer; a different explicit
tracer is rejected so the destination store is never ambiguous. See
[OpenAI / Anthropic](../integrations/llm-sdks.md).
