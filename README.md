# clew

> **git for AI reasoning.** Trace, branch, replay, and diff your agent runs — locally, content-addressed, with a portable bundle format.

[![1.0.0](https://img.shields.io/badge/version-1.0.0-blue.svg)]()
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org)
[![uv](https://img.shields.io/badge/built%20with-uv-6c47ff.svg)](https://docs.astral.sh/uv/)

---

## The problem

You shipped an AI agent. It works. Mostly. When it doesn't:

- You have no way to **see** what it was thinking, step by step.
- You have no way to **reproduce** a bad run.
- You have no way to **branch** a reasoning trace and try a different model.
- You have no way to **diff** two runs to see *exactly* what changed.

Current tools — LangSmith, Arize, Langfuse, agentlens — observe *telemetry*: timing, tokens, cost. They tell you *what happened* but not *why*. And they're mostly cloud.

## The clew insight

Apply git's Merkle DAG + content-addressing + branching to AI reasoning traces.

Every step is a content-addressed span. Steps form a DAG by parent pointers. Branches are named pointers into the DAG. Replay re-executes a span through any executor. Diff compares two DAGs by path. Sharing is a signed tarball.

**clew is the first tool that lets you `git checkout` a reasoning trace and try a different path.**

---

## Quickstart (30 seconds)

```bash
# Install
uv add clew

# Or: pip install clew

# Initialize a store
cd my-project
clew init

# Run the example agent (or use the SDK in your own)
uv run python -c "
from clew.sdk import Tracer, SpanType
t = Tracer()

@t.agent
def run():
    @t.span('plan', type=SpanType.DECISION)
    def plan(): return {'query': 'what is clew'}
    return plan()

run()
"

# Inspect
clew log                              # list traces
clew show <trace_id>                  # span tree
clew branch experiment <span_id>      # fork at a span
clew replay <trace_id>               # re-execute, get new trace
clew diff <orig> <new>               # see what changed
clew share <trace_id>                # export .clew.tgz
clew tui                             # launch the browser
```

---

## Killer features

- **Git-style branching.** Fork a reasoning trace at any span. Two branches from the same root are independent. HEAD points to the current branch.
- **Content-addressed.** Every span is hashed. Same input → same id. The store dedupes automatically.
- **Append-only, immutable.** Spans are never mutated. Branching is just moving a pointer. Originals are sacred.
- **Replay with any executor.** A `MockExecutor` re-uses recorded outputs (deterministic). A `RecordingExecutor` calls your async function. Replay never mutates the original — it creates a new trace.
- **Structural diff.** Match spans by their path from the root, not by id. Two replays with different models show "modified", not "added" + "removed".
- **OTel-compatible.** Reads and writes `gen_ai.*` attributes. Instrument an OpenAI or Anthropic client in two lines.
- **Portable signed bundles.** `clew share` → `.clew.tgz` with a manifest, all spans, and a SHA-256 signature.
- **Single binary, zero cloud.** Runs on a Raspberry Pi. No accounts. No telemetry. Your traces stay on your disk.
- **Textual TUI.** A keyboard-driven browser for traces, branches, and diffs.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  your agent                                                │
│  ┌────────────────────────────────────────────────────┐  │
│  │  @t.agent  →  new trace_id, root span              │  │
│  │   └─ @t.span("plan", type=DECISION)                │  │
│  │       └─ @t.span("search", type=TOOL)              │  │
│  │           └─ @t.span("answer", type=LLM)            │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────┘
                           │ append-only JSONL
                           ▼
┌──────────────────────────────────────────────────────────┐
│  .clew/                                                    │
│  ├── spans/<id[:2]>/<id>.jsonl   ← content-addressed     │
│  ├── index.sqlite                ← queryable               │
│  ├── refs/<name>                 ← branches                │
│  ├── HEAD                        ← current branch          │
│  └── manifest.json               ← store metadata          │
└──────────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  clew CLI (single binary)                                  │
│  log · show · branch · branches · checkout · replay ·     │
│  diff · share · tui · init · version                      │
└──────────────────────────────────────────────────────────┘
```

Every span is hashed via canonical-JSON → SHA-256. Spans form a DAG by `parent_ids`. Branches are named pointers into the DAG (just like git refs). Replay rewrites the DAG with a fresh root; the original is untouched. Diff matches spans by their path from the root (concatenation of `span.name` along the parent chain), so two replays with different models show what really changed.

---

## Compare to the field

|                      | clew | LangSmith | Arize | Langfuse | agentlens |
| -------------------- | :--: | :-------: | :---: | :------: | :-------: |
| Local-first          |  ✅  |    ❌     |  ❌   |    ❌    |    ⚠️    |
| Git-style branching   |  ✅  |    ❌     |  ❌   |    ❌    |    ❌    |
| Content-addressed    |  ✅  |    ❌     |  ❌   |    ❌    |    ❌    |
| OTel-compatible      |  ✅  |    ✅     |  ✅   |    ✅    |    ✅    |
| Open source          |  ✅  |    ❌     |  ⚠️  |    ✅    |    ✅    |
| Single binary        |  ✅  |    ❌     |  ❌   |    ❌    |    ❌    |
| Time-travel replay   |  ✅  |    ⚠️    |  ⚠️  |    ⚠️    |    ✅    |
| Portable bundles     |  ✅  |    ❌     |  ❌   |    ❌    |    ❌    |
| Python SDK           |  ✅  |    ✅     |  ✅   |    ✅    |    ✅    |
| TUI                  |  ✅  |    ❌     |  ❌   |    ❌    |    ❌    |

clew is the only tool that combines **local-first + branching + content-addressing + portable bundles** in a single open-source binary.

---

## SDK

```python
from clew.sdk import Tracer, SpanType

t = Tracer()  # writes to ./.clew

@t.agent
def my_agent(question: str) -> str:
    @t.span("plan", type=SpanType.DECISION)
    def plan() -> dict:
        return {"query": question}

    @t.span("search", type=SpanType.TOOL)
    def search(plan: dict) -> list[str]:
        return ["result 1", "result 2"]

    @t.span("answer", type=SpanType.LLM)
    def answer(plan: dict, hits: list[str]) -> str:
        return f"{plan['query']}: {' '.join(hits)}"

    p = plan()
    s = search(p)
    return answer(p, s)

my_agent("What is clew?")
```

OpenAI / Anthropic auto-instrumentation:

```python
from openai import OpenAI
from clew.sdk import instrument_openai

client = OpenAI()
instrument_openai(client)

# Every chat.completions.create call now writes a span to .clew
client.chat.completions.create(model="gpt-4o", messages=[...])
```

---

## CLI

```
clew init [path]                    Initialize a .clew/ store
clew log [--json]                   List traces
clew show <trace_id> [--json]       Show span tree
clew branch <name> [<span_id>]      Create a branch at a span
clew branches                      List branches
clew checkout <name>                Switch current branch
clew replay <trace_id> [--from <span>] [--executor mock|recording]
clew diff <trace_a> <trace_b>       Structural diff
clew share <trace_id> [--out PATH]  Export signed bundle
clew tui                            Launch the interactive TUI
clew version                        Print version
```

Every command has `--help` and exits 0 on success.

---

## Why clew wins

The OSS winner thesis is straightforward:

- **Local-first is the future.** GDPR, data residency, and "we don't want our prompts on a vendor's server" are not edge cases.
- **Standards need a foothold.** MCP just won the protocol war. There is no equivalent for *trace format*. clew is the candidate — open, OTel-friendly, local-first, simple to vendor.
- **Network effects.** The more agents use clew, the more valuable it becomes: shared trace format, shared diffs, shared bundles. SQLite won the embedded DB war not by being the best but by being *everywhere*.
- **Tooling compounds.** A TUI today, a web viewer tomorrow, a hosted diff service the day after. Each layer pulls in the next wave of users.

clew is not just a tool. It's a candidate for **the trace format** that AI agents will speak. Local-first, content-addressed, OTel-friendly. The same role that SQLite plays for databases or git plays for source control.

---

## Contributing

PRs welcome. The bar is high: the architecture is small and every addition must justify its weight. Read `ARCHITECTURE.md` first.

Run the tests:

```bash
uv run pytest
```

Run the linter:

```bash
uv run ruff check .
uv run mypy --strict src/
```

---

## License

MIT — see [LICENSE](./LICENSE).
