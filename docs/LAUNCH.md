# 1.1.5 launch kit

Do not publish until the account-level containment steps, automated release gates, and
private-tester gate in the [release checklist](RELEASE.md) are complete. This page is an
operator playbook; Clew itself sends no adoption telemetry.

## Private demo round

Recruit at least ten Python-agent developers across custom agents, LangChain, MCP, and
local-first communities. At least five must try Clew on an agent they already own.

Ask each tester to run:

```bash
uvx clew-ai demo
uv add clew-ai
clew init
# instrument one existing agent, then:
clew log
clew show TRACE_ID
clew replay TRACE_ID --executor mock --branch first-replay
clew diff TRACE_ID REPLAY_TRACE_ID
clew doctor
```

Record outside the runtime: Python/OS, agent framework, install outcome, time to first
trace, replay/diff outcome, the first confusing step, whether they would use it again,
and any P0/P1 issue. Do not collect their trace payloads unless they deliberately redact
and share them.

## Reproducible launch assets

- [`clew-demo.gif`](assets/clew-demo.gif): a 52-second recording rendered from the real
  offline `clew demo` output.
- [`demo-trace.html`](assets/demo-trace.html): a fresh self-contained replay report.
- [Architecture](internals/architecture.md), [performance contract](BENCHMARKS.md),
  [security policy](https://github.com/samsamurai301/clew-ai/blob/main/SECURITY.md), and
  [v1 migration warning](migration.md).
- Three copy-paste workflows in the repository README: custom agent, replay/branch/diff,
  and provider instrumentation.

Regenerate the GIF from product output without adding Pillow to the package runtime:

```bash
uv run --with 'pillow>=11,<12' python scripts/render_demo_gif.py
```

## Positioning

Use this exact description:

> Clew is a zero-server, Git-like what-if debugger for Python agent traces.

Do not claim that Clew is the first or only agent debugger, a single binary, or a full
observability replacement. The README links directly to current LangGraph, LangSmith,
Phoenix, and Langfuse capabilities.

## Coordinated launch

Publish the same reproducible demo and compatibility warning to:

1. Show HN;
2. r/Python and r/LocalLLaMA, following each community's self-promotion rules;
3. relevant Python-agent, LangChain, MCP, and local-first Discord communities;
4. X and LinkedIn; and
5. Python/AI newsletters that accept developer-tool submissions.

Suggested short copy:

> I built Clew, a zero-server what-if debugger for Python agent traces. It records runs
> locally, replays a full trace or suffix through a constrained executor, branches the
> result, and structurally diffs repeated spans without an account or collector. The
> offline demo is `uvx clew-ai demo`. Version 1.1.5 deliberately refuses the old v1 store
> format rather than risking silent corruption.

Follow with technical notes on the occurrence-ID collision lesson, replay topology,
local plaintext/privacy boundaries, and signed portable traces. Submit focused examples
to ecosystem repositories and curated agent/MCP lists only when they use their real API
and pass their maintainers' tests.

## First two weeks

- Triage new issues daily and ask for the smallest reproducible trace or mock program.
- Respond to reproducible bugs within 24 hours.
- Yank 1.1.5 and release 1.1.6 for a critical regression; never replace an artifact.
- Keep public status honest: label known limitations and do not publish tester trace data.

## Ninety-day funnel

The primary target is 1,000 GitHub stars. Review weekly:

- GitHub stars, unique visitors, unique cloners, contributors, and repeat issue or
  discussion participants;
- aggregate `clew-ai` downloads from public PyPI statistics; and
- documentation visits from an explicitly configured, privacy-reviewed Pages analytics
  source, if one is enabled.

These are external aggregate signals. Never infer adoption by adding a network request to
the Clew runtime, and never enable documentation analytics silently.
