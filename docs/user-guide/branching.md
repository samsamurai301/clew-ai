# Branching & replay

The two killer features of clew are **branching** and **replay**.
This page shows what they are, why they matter, and how to use
them.

## Why branch?

When your agent does something unexpected, the natural question
is "what would have happened if X had been different?" where X
is one of:

- The model (`gpt-4o` vs `gpt-4o-mini`).
- The prompt template.
- A tool implementation.
- The order of operations.

The only way to answer that question in production is to *re-run
the agent with the change and see*. The only way to answer it
*quickly* is to *branch the trace and replay just the parts that
would change*.

## What a branch is

A branch is a named pointer to a span id:

```
$ cat .clew/refs/main
6f8a3b2c1d9e4f5a...

$ cat .clew/refs/alt
8d4c1e2f3a9b4c5d...
```

The on-disk file is just a 64-char hex string — the span id.
That's it. No DAG magic, no metadata, no "is this branch ahead
of main?" clew's branches are git-style: a name, a target,
nothing more.

## What a trace is

A trace is a Merkle DAG of spans. Every span has a list of
`parent_ids` (zero or more). The DAG forms a tree when there's
exactly one root, or a more general graph when there are merges.

Two spans with the same content have the same id (content
addressing). This means:

- A new run that re-executes the same code with the same inputs
  produces a span with the same id. The store doesn't grow.
- A branch that shares an ancestor with `main` is not a copy —
  it's a fork. The shared part is one file on disk.

## Creating a branch

```bash
# Branch from the current HEAD
clew branch alt

# Branch from a specific span
clew branch alt <span_id>

# Branch from a specific trace
clew branch alt --from-span <root_span_id>
```

The new branch is a sibling of `main`; both point at their
respective roots.

## Switching branches

```bash
clew checkout main
clew checkout alt
```

`HEAD` is a single line in `.clew/HEAD`. Checking out a branch
just writes its name to that file.

## Replay

`clew replay <trace_id>` runs the trace through a new
executor. The default executor is `MockExecutor` (returns the
recorded outputs), which gives you a deterministic re-execution
that's useful for testing.

```bash
clew replay <trace_id>
# returns a new trace id
```

`RecordingExecutor` records what was actually computed this
time. Pair it with the SDK's `@t.agent` decorator and you get
the original trace, the recorded output, and the fresh output
side-by-side.

## Diff

`clew diff <trace_a> <trace_b>` computes a structural diff
between two traces. Spans are matched by **path from the
root** (not by span id) so the diff is stable across replays
where some spans share an id (ancestors that didn't change).

The diff output is colored text by default and JSON with
`--json`:

```
--- 6f8a3b2c1d9e
+++ 8d4c1e2f3a9b
@@ 3 modified, +0 -0, 1 unchanged @@
~ run_agent ... 'gpt-4o' → 'gpt-4o-mini'
~ answer    ... 'clew is a debugger' → 'clew is a tool'
~ plan      ... 'plan: ...' → 'plan: ...'
  search    ... 'results for: ...' (unchanged)
```

## A real example

Run the bundled demo:

```bash
uv run python examples/research_agent.py
```

You'll see a 3-step agent run twice (once with `gpt-4o`, once
with `gpt-4o-mini`), branch, diff, replay, doctor, and query —
all in one script.

## See also

- [Diffing](diff.md) — what the diff engine matches
- [Sharing](sharing.md) — sending a branched trace to a teammate
- [Replay engine](../internals/replay.md) — how the replay works under the hood
