# Migrating from 0.1.0 to 1.0.0

This guide covers the differences between `clew 0.1.0` (the MVP
release) and `clew 1.0.0` (the first stable release). The on-disk
store format is unchanged; you can open a 0.1.0 store in 1.0.0
without any migration. Only the CLI surface and a few Python API
behaviors have changed.

## Python SDK

### The async agent bug

In 0.1.0, the async agent path was broken: an `@t.agent` over an
`async def` would await `None` (the partial result of
`_run_as_agent` before the function was called), causing a
`TypeError: object NoneType can't be used in 'await' expression`.

In 1.0.0, the agent has two distinct code paths:

```python
@t.agent
def run_sync(x): ...       # uses _run_sync_agent

@t.agent
async def run_async(x): ...  # uses _run_async_agent (new in 1.0.0)
```

If your 0.1.0 code was wrapped in `try/except` to work around the
bug, the workaround is no longer needed. The async path now
returns the function's actual return value.

### Generator span support (new in 1.0.0)

If you were iterating a generator manually inside a `@t.span`,
1.0.0 will do it for you:

```python
# 0.1.0
@t.span("stream")
def consume():
    items = []
    for x in generate():
        items.append(x)
        # no per-item tracing
    return items

# 1.0.0 — span starts on first iteration, ends on exhaustion
@t.span("stream")
def stream():
    for x in generate():
        yield x  # each yield is captured as stream.item-N
```

The same works for `async def` + `yield` (async generators).

## CLI

### `clew share` now requires `--key`

The 0.1.0 release exported unsigned tarballs. The 1.0.0 release
generates real Ed25519 signatures, which requires a private key.

```bash
# 0.1.0
clew share <trace_id> --out bundle.tgz

# 1.0.0
clew keygen --out clew-key.pem
clew share <trace_id> --key clew-key.pem --out bundle.tgz
```

The private key is written unencrypted (mode 0600). Treat it
like a password: keep it out of git, store it in your password
manager.

### New commands

| Command                  | What it does                                    |
| ------------------------ | ----------------------------------------------- |
| `clew keygen`            | Generate an Ed25519 keypair (private + public)  |
| `clew verify`            | Verify a signed bundle                          |
| `clew import`            | Verify + import a signed bundle                 |
| `clew doctor`            | Check store health (read-only)                  |
| `clew gc`                | Remove orphan span files (`--dry-run` supported) |
| `clew query`             | Search spans by name/type/status/metadata       |
| `clew export`            | Write a trace to OTel NDJSON                    |
| `clew otel-import`       | Read OTel NDJSON into the store                 |
| `clew trace -- <cmd>`    | Run a subprocess as a single span               |

### OTel NDJSON vs signed bundles

`clew 1.0.0` has two ways to move traces between machines:

- **`clew share` / `clew import`** — signed, cryptographic
  integrity, content-addressed. The "send your agent's trace to
  a teammate" workflow.
- **`clew export` / `clew otel-import`** — unsigned, OTel-shaped
  NDJSON, one file per trace. The "send your trace to an OTel
  collector" or "import a trace from an OTel-instrumented
  agent" workflow.

Use `share` for trusted handoffs within a team. Use `export` for
interop with the wider OTel ecosystem.

## On-disk store

No changes. A 0.1.0 store opens cleanly in 1.0.0. New stores
created in 1.0.0 have a placeholder `refs/main` (64 zeros) so
the doctor doesn't flag a fresh store as having a dangling HEAD;
this file is overwritten the first time you move `main` onto a
real span.

## Timeline of changes

- **0.1.0**: MVP, unsigned bundles, broken async agent
- **0.2.0** (skipped; we went straight to 1.0.0)
- **1.0.0**: signed bundles, working async, generator support,
  doctor/gc/query/export/import/trace, Ed25519, real docs

## What didn't change

- `Span`, `Trace`, `Branch`, `Ref` model classes — same fields,
  same validators, same JSON shape.
- `@t.agent` and `@t.span` decorator signatures — backward
  compatible.
- The store layout (`spans/`, `refs/`, `HEAD`, `index.sqlite`,
  `manifest.json`) — bit-for-bit the same.
- The OTel mapping (`gen_ai.*` attributes, status codes,
  millisecond precision) — same as 0.1.0.
- The replay engine's mock + recording executors — same protocol.
- The diff engine's path-based matching — same algorithm.
