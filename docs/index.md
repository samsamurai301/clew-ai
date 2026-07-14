---
hide:
  - navigation
  - toc
---

<div align="center" markdown>

# clew

**git for AI reasoning.**

Trace, branch, replay, and diff your agent runs — locally, content-addressed, with a portable bundle format.

[![PyPI version](https://img.shields.io/pypi/v/clew.svg)](https://pypi.org/project/clew/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-315%20passing-brightgreen.svg)](#)
[![Coverage](https://img.shields.io/badge/coverage-87%25-brightgreen.svg)](#)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

[Get started :material-rocket-launch:](getting-started/install.md){ .md-button .md-button--primary }
[View on GitHub :fontawesome-brands-github:](https://github.com/clew/clew){ .md-button }

</div>

---

## The problem

You shipped an AI agent. It works. *Mostly.* When it doesn't:

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

## Quickstart

```bash
pip install clew
```

```python
from clew.sdk import Tracer, SpanType

t = Tracer()  # uses ./.clew

@t.agent
def run(question: str) -> str:
    @t.span("plan", type=SpanType.LLM)
    def plan():
        return "step by step: " + question

    @t.span("answer", type=SpanType.LLM)
    def answer(plan_text: str):
        return "the answer based on: " + plan_text

    return answer(plan())
```

Then:

```bash
clew log                  # list every trace
clew show <trace_id>      # the span tree
clew branch alt <span>    # branch the trace
clew replay <trace_id>    # re-execute, new trace
clew diff <a> <b>         # what changed
clew show <trace_id> --html report.html  # share as HTML
```

---

## Why clew

| | LangSmith | Arize | Langfuse | agentlens | **clew** |
|---|---|---|---|---|---|
| Local-first | ❌ | ❌ | ❌ | ❌ | ✅ |
| Open source | partial | partial | ✅ | partial | ✅ |
| Trace **branching** | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Replay** with new code | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Content-addressed** dedup | ❌ | ❌ | ❌ | ❌ | ✅ |
| Git-style sharing | ❌ | ❌ | ❌ | ❌ | ✅ |
| OTel-compatible | ✅ | ✅ | ✅ | ❌ | ✅ |
| Cloud backend | ✅ | ✅ | ✅ | ❌ | opt-in |
| **MCP server** | ❌ | ❌ | ❌ | ❌ | ✅ |

---

## What's in the box

<div class="grid cards" markdown>

-   :material-git:{ .lg .middle } **Branching**

    ---

    Fork any reasoning trace at any span. Try a different model, a
    different prompt, a different tool. Diff the two.

    [:octicons-arrow-right-24: Branching & replay](user-guide/branching.md)

-   :material-graph:{ .lg .middle } **Content-addressed DAG**

    ---

    Every span is `sha256(canonical_json(content))`. Two identical
    inputs collapse to one id. Replay is free of duplication.

    [:octicons-arrow-right-24: Architecture](internals/architecture.md)

-   :material-file-sign:{ .lg .middle } **Signed bundles**

    ---

    Share a trace with a teammate. Ed25519 signature over the
    manifest, content hash over the spans, `clew verify` to check.

    [:octicons-arrow-right-24: Sharing](user-guide/sharing.md)

-   :material-magnify:{ .lg .middle } **Search**

    ---

    `clew query --type LLM --status ERROR --metadata model=gpt-4o`
    finds the exact trace you want across thousands.

    [:octicons-arrow-right-24: Querying](user-guide/query.md)

-   :material-microsoft-azure-devops:{ .lg .middle } **MCP server**

    ---

    `clew mcp` exposes your store to Claude Desktop, Cursor, Cline.
    Talk to your traces from the conversation.

    [:octicons-arrow-right-24: MCP](integrations/mcp.md)

-   :material-language-python:{ .lg .middle } **Python SDK**

    ---

    `@t.agent` and `@t.span` decorators. Sync, async, generators.
    Drop-in tracing for OpenAI, Anthropic, and LangChain.

    [:octicons-arrow-right-24: SDK reference](reference/sdk.md)

-   :material-file-document-outline:{ .lg .middle } **HTML reports**

    ---

    `clew show <id> --html` writes a self-contained, interactive
    HTML page. Email it, gist it, post it on Slack.

    [:octicons-arrow-right-24: Exporting](user-guide/export.md)

-   :material-doctor:{ .lg .middle } **Doctor & GC**

    ---

    `clew doctor` walks the store and reports corruption, dangling
    refs, missing spans. `clew gc` cleans up the orphans.

    [:octicons-arrow-right-24: Doctor & GC](user-guide/doctor.md)

</div>

---

## Install

```bash
pip install clew
# or
uv add clew
# with MCP support
uv add 'clew[mcp]'
```

Python 3.11+. No system dependencies. Optional `cryptography>=43` is
already a default dep for signed bundles.

[:octicons-arrow-right-24: Full installation guide](getting-started/install.md)
