# Changelog

All notable changes to `clew` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [1.1.3] — 2026-07-14

The polish release. Every previously-flagged issue is fixed.

### Fixed

- CHANGELOG dates for 0.1.0, 1.0.0, 1.1.0, 1.1.1.
- README version badge text/URL mismatch.
- README: ``What's new in v1.1.2`` section added.

### Hardening

- **TOCTOU + symlink defense in `Store.put`**: writes go through
  a temp file with `O_CREAT | O_EXCL | O_NOFOLLOW` and are renamed
  atomically into place. Symlinks at the destination are refused
  (EEXIST/EACCES); partial writes are never observable.
- **`clew trace --clean-env`** flag: subprocess starts with a
  minimal environment (PATH, HOME, LANG, LC_ALL) instead of
  inheriting the full parent env (which may contain secrets).

### Tests

- +10 MCP tool tests (covers all 12 tools + 2 resources).
- +10 OTel SDK tests (fake-client for `instrument_openai` /
  `instrument_anthropic`, idempotency, exception capture).
- +9 langchain error-path tests (`on_*_error` callbacks, no-op on
  unknown run_id, span type classification).
- +2 store security tests (symlink refusal, no partial writes).
- +1 CLI test (`clew trace --clean-env` doesn't leak secrets).
- 350 total tests, 87% coverage, mypy --strict clean across 29 source files.

### Documentation

- `.github/CODEOWNERS` (security team for sensitive code).
- `.github/dependabot.yml` (weekly pip + GitHub Actions updates).
- `mkdocs build --strict` verified — no broken links, no missing pages.
- `pip-audit` against the resolved 1.1.3 dep tree: no known vulnerabilities.

## [1.1.2] — 2026-07-14

The security-hardening release. No public API changes; every change
is a defense against a known attack class.

### Hardening

- **Bundle extraction (CVE-2025-4138 / 4330 / 4517 / 7774)**:
  `verify_bundle` and `extract_spans` now apply a strict allowlist
  of tar member names. Bundles containing symlinks, hard links,
  device files, FIFOs, absolute paths, `..` traversal, or names
  with NUL/newline characters are refused. The decompressed size
  is capped at 256MB and member count at 1M (both overridable).
- **Span-id path traversal**: `Store._span_path` validates the id
  is lowercase hex (8-64 chars) before resolving the on-disk
  path. A span whose id contains `/`, `..`, or any non-hex char
  cannot be persisted.
- **Branch name hardening**: `BranchManager._ref_path` rejects
  names with `/`, `\`, NUL, control characters, or leading `.`
  (which would hide from `ls`). `BranchManager.list()` refuses
  to follow symlinks in `refs/`. `current()` validates the
  contents of `HEAD` against the same rules.
- **HTML report XSS defense**: `render_html` now HTML-entity-
  encodes `{` and `}` in user-supplied values, blocking an
  attack that re-substitutes template placeholders.
- **NDJSON bomb defense**: `import_ndjson` and `read_ndjson`
  enforce a 64MB cap on input size and a 1M cap on span count.
- **Tarball member hygiene**: bundle `TarInfo` entries have
  `uid`, `gid`, `uname`, `gname` zeroed so a tampered tar
  cannot impersonate a privileged owner.

### Documentation

- **`SECURITY.md`** (new): threat model, hardening checklist,
  supported-versions table, and credits.

### Tests

- 25 new security-focused tests covering the bundle allowlist,
  branch name validation, span id path traversal, subprocess
  argv handling, HTML template escaping, and NDJSON bomb
  defense. 315 total tests, 87% coverage, mypy --strict clean.

## [1.1.0] — 2026-07-13

The ecosystem release. `clew` now ships integrations for the
three most common LLM runtimes (MCP, LangChain, GitHub Actions)
and a portable HTML viewer for sharing traces by email.

### Added

- **MCP server** (`clew mcp`). 12 tools (list/get/search/diff/
  branch/checkout/replay/etc.) and 2 resources over the Model
  Context Protocol. Connects to Claude Desktop, Cursor, Cline,
  the MCP Inspector. Install with `uv add 'clew[mcp]'`.
- **HTML reports** (`clew show <id> --html <path>`). A single
  self-contained interactive page — collapsible tree, ERROR
  highlights, input/output on demand. Drop in an email, S3
  bucket, or GitHub gist.
- **LangChain callback handler** (`clew.integrations.langchain
  .ClewCallbackHandler`). Auto-instrument chains, LLMs, and
  tools without changing your application code.
- **GitHub Action** (`.github/actions/clew-trace/action.yml`).
  A composite action that records a trace during CI runs and
  uploads the HTML report as an artifact.
- **Real LLM example** (`examples/real_llm_agent.py`). Calls
  OpenAI / Anthropic for real when keys are set; falls back to
  a mock LLM otherwise.
- **mkdocs site** (`docs/` + `mkdocs.yml`). Full documentation:
  getting started, user guide, integrations, internals,
  reference, community.
- **Issue templates + PR template + FUNDING.yml** for GitHub.

### Fixed

- Fresh `clew init` no longer leaves a dangling HEAD.
- Bundle `load_pem_*_key` errors now raise `ValueError`
  consistently.

## [1.1.1] — 2026-07-13

The polish release. No public API changes; everything is a
tightening, a new doc page, or a developer-experience fix.

### Added

- **`clew bench`** — runs the in-process scaling benchmark
  with `--out` JSON. Reports timings for record / diff / gc
  on a fresh tempdir.
- **Polished HTML report** — stats panel (span count, error
  count, max depth, total time), search/filter box, expand-all
  and collapse-all buttons.
- **Three new internals docs**: `content-addressing.md`,
  `replay.md`, `bundle-format.md`. The on-disk store layout
  is now documented under `docs/reference/protocol.md`.
- **Streaming LLM example** (`examples/streaming_agent.py`).
  Shows how to record every token chunk as a child span,
  matching how OpenAI and Anthropic streaming SDKs work.
- **Docstring sweep** on previously undocumented public APIs.
- **Full `pyproject.toml` polish**: upper-bound version pins
  (`pydantic>=2.11,<3`, etc.), full classifiers (Production /
  Stable, full Python 3.11/3.12/3.13), `[project.urls]` with
  Changelog + MCP, `[project.optional-dependencies]` with
  `mcp` / `all` / `dev` extras, `[dependency-groups]` for
  uv-native docs/dev, full ruff rule selection, full mypy
  per-module overrides, `[tool.pytest.ini_options]` with
  `slow` and `integration` markers, `[tool.coverage]` with
  `fail_under=70`.

### Fixed

- **`clew --version` and `clew -V`** work again (click 8.4+
  removed the default `--version`; clew now adds an explicit
  typer callback with `is_eager=True`).
- **`__version__` is now dynamic** — read from
  `importlib.metadata`, no more hard-coded 1.0.0.

## [1.0.0] — 2026-07-13

The first stable release. `clew` is now production-ready for
local-first agent debugging. Every public API is stable; only
additive changes will land in 1.x.y.

### Added

- **Ed25519 bundle signing.** `clew share` now produces genuinely
  signed bundles. The manifest is signed with the user's private
  key; tampering with either the manifest or the span content is
  caught by `clew verify`. New commands: `clew keygen`, `clew
  verify`, `clew import`.
- **`clew doctor`** walks the store and reports manifest
  corruption, missing refs, dangling branches, and index/store
  divergence. Exits 0 when the store is healthy, 1 on errors.
- **`clew gc`** removes span files that are no longer reachable
  from any branch. Supports `--dry-run`.
- **`clew query`** searches spans by name, type, status,
  trace_id, and metadata key=value. The `--json` form is
  pipeable into `jq`.
- **`clew export`** writes a trace to OTel-compatible NDJSON
  with a `_kind: trace` header followed by one OTel span per
  line.
- **`clew otel-import`** reads OTel NDJSON (clew's own or a
  bare OTel stream) and adds the spans to the local store.
  Optionally creates a branch pointing at the imported root.
- **`clew trace -- <cmd>`** records an arbitrary subprocess as a
  single span. Useful for one-off agents that don't import clew.
- **Generator / async generator span support.** `@t.span` now
  wraps both regular functions and generator functions. A
  generator span starts on first iteration and ends on
  exhaustion; each yielded item gets its own child span.
- **Per-item child spans.** Streaming responses are now fully
  traced: every `yield` becomes a `name.item-N` observation span
  with the yielded value as the span's `output`.
- **OTel bridge accepts a tracer.** `instrument_openai(client,
  tracer=t)` and `instrument_anthropic(client, tracer=t)` let
  you share an existing `Tracer` rather than create a new one
  per call.
- **Store auto-creates a placeholder `main` ref.** A fresh
  `clew init` no longer reports a dangling HEAD; the default
  branch exists with a placeholder that is overwritten on the
  first `clew branch` / `clew checkout`.
- **New `cryptography>=43` runtime dependency** for Ed25519
  bundle signing.
- **`CONTRIBUTING.md`, `LICENSE` (MIT), `docs/FAQ.md`.**

### Changed

- **`clew share` now requires `--key <private-key>`.** The
  v0.1.0 release exported unsigned tarballs. Run `clew keygen`
  once and store the key securely; share the public half with
  anyone who needs to verify your bundles.
- **Async agent path is now strictly async.** The `@t.agent`
  decorator detects coroutine functions and uses a separate
  async path internally. The bug where `await self._run_as_agent`
  could see `result = None` is fixed; `_run_sync_agent` and
  `_run_async_agent` are distinct methods.

### Fixed

- The async agent wrapper no longer swallows the function's
  coroutine (it now `await`s the actual coroutine, not the
  return value of `_run_as_agent`).
- The branch manager's `iter_spans` reachability check now uses
  the actual Store API instead of a missing private helper.
- The doctor no longer flags the placeholder default-branch
  ref as dangling.

### Migration from 0.1.0

The on-disk store format is unchanged. You can open a 0.1.0
`.clew` directory in 1.0.0 without migration.

Two CLI commands changed in 1.0.0:

| 0.1.0                         | 1.0.0                                          |
| ----------------------------- | ---------------------------------------------- |
| `clew share <trace>`          | `clew share <trace> --key priv.pem` (required) |
| (no equivalent)               | `clew verify <bundle> --public-key pub.pem`    |
| (no equivalent)               | `clew import <bundle> --public-key pub.pem`    |
| (no equivalent)               | `clew doctor` / `clew gc`                      |
| (no equivalent)               | `clew query` / `clew export` / `clew otel-import` |
| (no equivalent)               | `clew trace -- <cmd>`                          |

The Python SDK is backward compatible: any code that worked with
`clew 0.1.0` continues to work in 1.0.0. The async agent path
behaves correctly now, which means code that *should* have
worked but didn't (because of the swallowed-coroutine bug) will
work without changes.

### Test coverage

246 tests across 15 test files. 87% line coverage. Every
public API has at least one direct test; the CLI is exercised
end-to-end via `typer.testing.CliRunner`.

## [0.1.0] — 2026-07-13

The initial MVP. Content-addressed store, git-style branching,
replay engine, structural diff, Python SDK, typer/rich CLI,
textual TUI, OTel-compatible format, signed-bundle export.
