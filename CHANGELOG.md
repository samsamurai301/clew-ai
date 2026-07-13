# Changelog

All notable changes to `clew` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-XX-XX

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

## [0.1.0] — 2026-01-XX

The initial MVP. Content-addressed store, git-style branching,
replay engine, structural diff, Python SDK, typer/rich CLI,
textual TUI, OTel-compatible format, signed-bundle export.
