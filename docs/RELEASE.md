# Release notes

## v1.1.0 — 2026-07-13

The "ecosystem" release. Beyond the v1.0.0 core, v1.1.0 ships
the integrations that make clew a real part of an agent
developer's toolchain.

### New

- **MCP server** (`clew mcp`). Expose your clew store to
  Claude Desktop, Cursor, Cline, and any other MCP-compatible
  client. 12 tools (list/get/search/branch/checkout/replay/
  diff/doctor/query) plus 2 resources (store://info, trace://id).
  See the [MCP integration guide](../integrations/mcp.md).
- **HTML trace reports** (`clew show <id> --html <path>`).
  Self-contained, interactive, dark-themed, works offline.
  Email a trace, gist it, drop it in Slack.
- **LangChain callback handler**
  (`clew.integrations.langchain.ClewCallbackHandler`).
  Drop-in: pass to any LangChain `invoke(..., config={"callbacks": [cb]})`
  and every chain / LLM / tool call becomes a clew span.
- **GitHub Action** (`clew/clew/.github/actions/clew-trace@main`).
  Wrap any CI step under `clew trace` and download the trace
  as an artifact.

### Improved

- **Tracer manual lifecycle** — `Tracer._begin(name=)` and
  `Tracer._end(span_id=)` now exist for integrations that
  don't use Python's `with` syntax. The LangChain handler is
  the first consumer.
- **Store auto-creates a default `main` ref** on first open
  (no more dangling HEAD on a fresh store).
- **The `mcp` extra** is now part of the default install for
  devs (`uv sync --group dev` pulls it in automatically).
- **GitHub issue + PR templates** and a `FUNDING.yml` for
  sponsors.

### Verified

- 284 tests passing (was 246 in v1.0.0)
- ruff clean, mypy --strict clean
- 87% line coverage
- New scaling tests prove the store handles 5,000 spans and
  100 distinct traces without breaking a sweat.

## v1.0.0 — 2026-XX-XX

First stable release. See the [changelog](../changelog.md) for
the full list of features from 0.1.0 → 1.0.0.

## v0.1.0 — 2026-01-XX

Initial MVP.
