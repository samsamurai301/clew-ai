# Examples

Five runnable demos. Pick the one that matches what you want to see.

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

## `research_agent.py` — multi-trace research workflow with branching

A "research agent" that plans, searches, fetches, and answers. Runs
twice with different models (`gpt-4o` and `gpt-4o-mini`), branches
the trace at the LLM call, diffs the two runs, and replays the
original through a mock executor. Uses a mock LLM — no API key
required.

```bash
uv run python examples/research_agent.py
```

## `real_llm_agent.py` — call OpenAI / Anthropic for real

Same agent as `research_agent.py` but makes real API calls if
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set in the environment.
Falls back to a mock LLM if neither is set, so the demo is always
runnable.

```bash
# with OpenAI
export OPENAI_API_KEY=sk-...
uv run python examples/real_llm_agent.py

# with Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
uv run python examples/real_llm_agent.py

# mock fallback
uv run python examples/real_llm_agent.py
```

## `streaming_agent.py` — instrument a streaming chat completion

Streams a chat completion token-by-token (mock LLM, no API key
required). Each token gets its own sub-span, so the resulting trace
has ~20 child spans under the `chat_completion` LLM span. This
mirrors how OpenAI's and Anthropic's streaming SDKs work — the
example swaps out `_mock_stream` for a real client and the trace
shape stays the same.

```bash
uv run python examples/streaming_agent.py
```
