# SDK Reference

The clew Python SDK is the user-facing tracing surface. The CLI is a
thin shell over the SDK; most users will spend their time in code, not
in a terminal.

## Public API

```python
from clew.sdk import (
    Tracer,                # the main user-facing tracer
    SpanType, SpanStatus,  # span enums
    Span, Trace,           # data models
    current_span,          # task-local span context helpers
    current_trace_id,
    OTelBridge,            # OTel format converter
    instrument_openai,     # monkey-patch helpers
    instrument_anthropic,
)
```

## The `Tracer` class

```python
class Tracer:
    def __init__(
        self,
        store: TraceStore | None = None,
        name: str = "default",
        cwd: Path | None = None,
    ) -> None: ...

    @property
    def store(self) -> TraceStore: ...

    def agent(self, fn): ...          # decorator: marks the trace entry
    def span(self, name, type=...):   # decorator factory: marks a child span
    def trace(self, name, type=...):  # context manager: manual span
```

### `@t.agent`

Marks the *entry point* of a trace. The decorated function becomes
the trace's root span. The trace id is the root span's id (content-addressed).

```python
@t.agent
def run_agent(question: str) -> str:
    ...
```

### `@t.span(name, type=SpanType.OBSERVATION)`

Wraps a function (sync or async) as a child span. The parent is
whichever span was active when the wrapped function was called.

```python
@t.span("search", type=SpanType.TOOL)
def search_web(query: str) -> list[str]:
    ...
```

### `with t.trace(...) as span:`

Open a span as a context manager — useful when you want to record a
block of code without decorating a function.

```python
with t.trace("my-block", type=SpanType.TOOL) as span:
    result = do_work()
    span.set_attribute("k", "v")
    span.set_output(result)
```

## Span types

`SpanType` is a `StrEnum` with four values:

- `LLM` — a chat-completion call
- `TOOL` — a tool/function invocation
- `DECISION` — an explicit branching point
- `OBSERVATION` — anything else (default)

```python
from clew.sdk import SpanType
SpanType.LLM        # → SpanType.LLM
SpanType.TOOL       # → SpanType.TOOL
```

## OTel auto-instrumentation

```python
from openai import OpenAI
from clew.sdk import instrument_openai

client = OpenAI()
instrument_openai(client)

# Every chat.completions.create call now writes a span with
# gen_ai.system="openai", gen_ai.request.model=..., etc.
client.chat.completions.create(model="gpt-4o", messages=[...])
```

`instrument_anthropic` works the same way for the Anthropic SDK.
Both helpers are no-ops if the underlying library is not installed.

## Context helpers

```python
from clew.sdk import current_span, current_trace_id

current_span()      # the span currently in scope, or None
current_trace_id()  # the trace id of the current span, or None
```

These are task-local: nested calls see the parent span, parallel
tasks see their own copy.

## Replay and diff

Replay and diff are first-class SDK operations:

```python
import asyncio
from clew.core.replay import ReplayEngine, RecordingExecutor
from clew.core.diff import diff, format_text

t = Tracer()

# Record a trace.
@t.agent
def run():
    @t.span("answer")
    def a(): return "hi"
    return a()
run()

trace_ids = list(t.store.store.iter_traces())
trace_id = trace_ids[0]

# Replay under a different "model" by providing a custom executor.
async def fn(span, ctx):
    return ("replayed-" + (span.output or "")), {"replay.model": "gpt-4o-mini"}

engine = ReplayEngine(t.store, executor=RecordingExecutor(fn))
new_trace = asyncio.run(engine.replay(trace_id))

# Diff.
a = t.store.get_trace(trace_id)
b = t.store.get_trace(new_trace.trace_id)
print(format_text(diff(a, b)))
```

## Branching

```python
from clew.core.branch import BranchManager

bm = BranchManager(t.store)
trace = t.store.get_trace(trace_id)
bm.create("experiment", trace.root_span_id)
bm.checkout("experiment")
```
