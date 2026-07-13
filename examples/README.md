# Examples

Two runnable demos. Pick the one that matches what you want to see.

## `simple_agent.py` — trace a 3-step agent

A trivial agent that plans, calls a fake search tool, and composes an
answer. Uses `@t.agent` and `@t.span` decorators. Outputs a summary
to stdout and writes the trace to `./.clew`.

```bash
uv run python examples/simple_agent.py
```

## `branching_demo.py` — branch + replay + diff

Runs a 3-step "agent" with a parameter (`model="gpt-4o"`), branches
at a mid-trace span, replays the branch with a different parameter
(`model="gpt-4o-mini"`), and prints a structural diff of the two
traces. This is the killer-feature demo.

```bash
uv run python examples/branching_demo.py
```

Expected output (truncated):

```
Original: [gpt-4o] What is clew?
Replayed: dd424866907d...

--- trace 684a6023f0e8431dac13c718afe6e3fd
+++ trace dd424866907d4ef3b07a49c00bdaa835
@@ 3 modified, +0 -0, 0 unchanged @@
~ run_agent (input=..., output=... -> '[gpt-4o-mini] What is clew?')
~ answer (...)
~ plan (...)
```
