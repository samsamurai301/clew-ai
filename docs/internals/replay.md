# Replay engine

Replay produces a new trace; it never mutates the source trace.

## CLI

```bash
clew replay TRACE --executor mock
clew replay TRACE --executor module:function --from SPAN --branch NAME
```

`mock` is deterministic and offline. A Python executor may be synchronous or asynchronous:

```python
from clew.sdk import ReplayContext, ReplayResult, Span

async def execute(span: Span, context: ReplayContext) -> ReplayResult:
    return ReplayResult(
        output={"source": span.output, "parents": [p.output for p in context.parent_chain]},
        attributes={"replay.executor": "candidate"},
    )
```

The callable must return `ReplayResult`. It cannot choose the destination ID, trace ID,
parents, sequence, timestamp, status, or hash.

## Topology algorithm

1. Validate source identity, parent completeness, sequence uniqueness, root count, and DAG.
2. Select the complete trace, or the requested span plus descendants and ancestor closure.
3. Allocate fresh destination IDs for every included source occurrence.
4. Clone ancestors for partial replay with rewritten destination parents.
5. Execute selected occurrences in deterministic topological/sequence order.
6. Build `ReplayContext.parent_chain` from finalized destination ancestors.
7. Persist each finalized result.

Every destination parent ID therefore belongs to the destination trace, including
multi-parent joins.

## Failure behavior

An executor exception is captured as `ERROR`. Descendants that depend on it are persisted
as `SKIPPED`; they are not called. The new trace is still persisted and its ID is printed.
The CLI exits nonzero so automation cannot mistake a diagnostic replay for success.

The source trace and any existing ref remain unchanged unless `--branch` explicitly creates
or moves the named branch after replay.
