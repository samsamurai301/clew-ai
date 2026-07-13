# Replay engine

The replay engine re-executes a trace and produces a new
trace id. The original is untouched. This page explains the
replay algorithm and the two executors clew ships.

## What replay does

1. Find the root span of the source trace.
2. Walk the source trace's DAG and copy every ancestor into
   a new trace. By content addressing, ancestors that are
   bit-identical to the original share the same id; the
   new trace's "ancestor" portion is just a shared subtree.
3. Re-execute the leaf spans (or the spans you specify via
   `--from`) through an executor. The executor returns
   new output for each leaf.
4. Finalize the new trace. The new trace is fully usable
   for branching, diffing, and replaying again.

## Why replay is interesting

The point of replay is **A/B testing without re-recording**.
You have a working trace, you want to know what would have
happened with a different model / prompt / tool — replay
the trace through a different *executor* (one that calls
your new code) and you get a fresh trace to diff against.

Replay with a `MockExecutor` is useful for testing the
replay engine itself: the mock returns the original
recorded output, so a replayed trace should be
*bit-identical* to the source. If it isn't, something
is wrong.

## Executors

clew ships two executors:

### `MockExecutor`

Returns the recorded output of each span. Useful for
deterministic re-execution and for testing.

```python
from clew.core.replay import MockExecutor, ReplayEngine
engine = ReplayEngine(store, executor=MockExecutor())
new_trace_id = engine.replay(root_span_id)
```

The mock executor reads the original span's `output` field
and returns it. If the span had no recorded output (e.g. it
raised), the mock returns the same exception. The result is
a new trace with identical content — same span ids, same
outputs, same statuses.

### `RecordingExecutor`

A pass-through executor that records whatever is computed
this time. Pair with the SDK's `@t.agent` decorator to
re-execute your code and capture the new outputs.

```python
from clew.core.replay import RecordingExecutor, ReplayEngine
executor = RecordingExecutor()
engine = ReplayEngine(store, executor=executor)
# The executor actually invokes your code; you get a fresh
# trace where leaf spans have new ids and new outputs.
new_trace_id = engine.replay(root_span_id)
```

The recording executor is what you'd use to actually
"replay with a different model" — your code runs again,
the new outputs get recorded, and the diff engine compares
the old and new outputs side by side.

## `--from <span>`

By default, replay re-executes every leaf. With `--from
<span>`, only the spans *descended from* `<span>` are
re-executed. The ancestors are kept (and share ids via
content addressing); only the descendants get new ids and
new outputs.

This is how you do "replay just the LLM call" without
re-running the whole agent.

## Diff after replay

After replay, the new trace is in the store. Run `clew diff
<old> <new>` to see what changed. The diff is path-based
(spans are matched by their path from the root, not by id)
so the comparison is stable across replays where ancestors
share ids and descendants diverge.

## See also

- [Architecture](architecture.md) — the trace DAG layout
- [Content addressing](content-addressing.md) — why replay
  is efficient
