# Changelog

All notable changes to clew are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-07-13

### Added

- Content-addressed trace storage (Merkle DAG of spans over JSONL)
- Git-style branching with refs, HEAD, and a default `main` branch
- Replay engine with `MockExecutor` (re-uses outputs) and `RecordingExecutor` (calls your async function)
- Structural trace diff with text and JSON output, matched by path-from-root
- Python SDK: `@t.agent` and `@t.span` decorators (sync + async), `with t.trace(...)` context manager
- OTel-compatible format with `gen_ai.*` attribute mapping
- OTel auto-instrumentation for OpenAI and Anthropic clients
- Local-first single-binary CLI built with `typer` + `rich`:
  - `clew init`, `version`, `log`, `show`
  - `clew branch`, `branches`, `checkout`
  - `clew replay`, `diff`, `share`
  - `clew tui` (textual-based interactive browser)
- Portable signed bundles (`.clew.tgz` with manifest + SHA-256)
- Comprehensive test suite: 122 tests across storage, semantics, SDK, CLI
- Documentation: `README.md`, `ARCHITECTURE.md`, `PROTOCOL.md`, `docs/QUICKSTART.md`, `docs/CLI.md`, `docs/SDK.md`
- GitHub Actions CI matrix on Python 3.11 / 3.12 / 3.13
- GitHub Actions release workflow (OIDC trusted publishing to PyPI)
- Examples: `examples/simple_agent.py`, `examples/branching_demo.py`

### Notes

- This is the initial MVP. Single binary, zero cloud, zero external services.
- Branching is the killer feature: no other open-source tool does
  git-style branching of AI reasoning traces.
- Tag: `v0.1.0`. Commit: see `git log v0.1.0 -1`.
