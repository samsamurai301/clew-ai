# CLI Reference

`clew` is a single binary exposing the full surface area of a clew
store. Every command has `--help` and exits 0 on success.

## Global options

```
--root PATH    Path to the .clew directory (default: ./.clew)
--json         Emit machine-readable JSON where applicable
```

## Commands

### `clew init [PATH]`

Initialize a new `.clew/` store under `PATH` (default: cwd). Idempotent.

```bash
$ clew init
Initialized clew store at /path/to/.clew
```

### `clew version`

Print the package version.

```bash
$ clew version
clew 0.1.0
```

### `clew log [--json]`

List all traces. Default: a rich table. With `--json`: NDJSON.

```bash
$ clew log
                              clew traces
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ trace id      ┃ root     ┃ spans ┃ started               ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ ea23b3f32bc1… │ answer   │     3 │ 2026-07-13T19:04:33…  │
└───────────────┴──────────┴───────┴──────────────────────┘

$ clew log --json
{"trace_id":"ea23b3f32bc1...","root_name":"answer","span_count":3,"started_at":"2026-07-13T19:04:33.067270+00:00"}
```

### `clew show <trace_id> [--json]`

Show the span tree of a trace.

```bash
$ clew show ea23b3f32bc1
ea23b3f32bc1…  (3 spans)
└── answer [OBSERVATION] OK  (1.0ms)
    ├── plan [DECISION] OK  (0.0ms)
    └── answer [LLM] OK  (0.0ms)
```

### `clew branch <name> [<from_span>]`

Create a branch pointing at a span. If `<from_span>` is omitted, the
current HEAD is used.

```bash
$ clew branch experiment abc...
Created branch 'experiment' → abc…
```

### `clew branches [--json]`

List all branches. The current branch is marked with `*`.

### `clew checkout <name>`

Switch the current branch (writes `.clew/HEAD`).

### `clew replay <trace_id> [--from <span>] [--executor mock|recording]`

Re-execute a trace, producing a new trace. The original is never mutated.
With `--from`, only that span and its descendants are re-executed;
ancestors are copied verbatim.

```bash
$ clew replay ea23b3f32bc1
<new-trace-id>
```

### `clew diff <trace_a> <trace_b> [--json]`

Diff two traces. Spans are matched by their path from the root, so two
replays with different content show as `modified` (not as added+removed).

### `clew share <trace_id> [--out PATH]`

Export a portable, signed bundle (`.clew.tgz`). Default output:
`<trace_id>.clew.tgz` in cwd.

```bash
$ clew share ea23b3f32bc1
/path/to/ea23b3f32bc1…clew.tgz

$ tar tzf ea23b3f32bc1…clew.tgz
manifest.json
spans/ab/abc....jsonl
...
```

### `clew tui`

Launch the interactive textual TUI.

```
┌─ clew ─────────────────────────────────────┐
│ left (traces)         │ right (span tree)  │
│                       │                   │
│                       ├───────────────────┤
│                       │ details            │
└───────────────────────┴───────────────────┘
```

Keybindings: `q` quit, `enter` expand, `b` branch, `r` replay, `d` diff.


### `clew keygen [--out PATH] [--public-out PATH]`

Generate a fresh Ed25519 keypair for signing bundles. The private key is written
unencrypted (mode 0600) — store it securely. The public key defaults to `<out>.pub`.

```bash
clew keygen --out ~/.clew/key.pem
# private key: /home/user/.clew/key.pem  (keep this secret)
# public  key: /home/user/.clew/key.pem.pub
```

### `clew share <trace_id> --key PRIV [--out PATH]`

Export a trace as a portable signed bundle (`*.clew.tgz`). The bundle contains
a manifest, an Ed25519 signature, and one file per span.

```bash
clew share 0a1b2c... --key ~/.clew/key.pem --out trace.tgz
```

### `clew verify <bundle> --public-key PUB`

Verify a signed bundle. Exits 0 on success, 1 on tamper or format error.

```bash
clew verify trace.tgz --public-key teammate-pub.pem
# valid  trace_id=0a1b2c...  spans=4  created_at=2026-XX-XXT...
```

### `clew import <bundle> --public-key PUB [--branch NAME]`

Verify and import a signed bundle. Optionally create a branch pointing at the
imported root.

```bash
clew import trace.tgz --public-key teammate-pub.pem --branch shared
```

### `clew doctor [--json]`

Check the store for manifest corruption, missing refs, dangling branches, and
index/store divergence. Read-only. Exits 0 on healthy, 1 on errors.

```bash
clew doctor
# ╭──── clew doctor ────╮
# │ head: main  branches: 2  spans: 42  │
# │ ok      -       no issues found      │
# ╰─────────────────────────────────────╯
```

### `clew gc [--dry-run] [--json]`

Remove span files that are no longer reachable from any branch. Use `--dry-run`
to preview.

```bash
clew gc --dry-run
# scanned 50 spans, would delete 3, kept 47
```

### `clew query [--name SUBSTR] [--type TYPE] [--status STATUS] [--trace ID] [--metadata k=v] [--limit N]`

Search spans by any combination of filters. The `--json` form is pipeable.

```bash
clew query --type LLM --status ERROR
clew query --name gpt-4o --metadata model=gpt-4o
```

### `clew export <trace_id> [--out PATH]`

Write a trace to OTel-compatible NDJSON.

```bash
clew export 0a1b2c... --out trace.ndjson
```

### `clew otel-import <ndjson> [--branch NAME]`

Read OTel NDJSON into the local store. Accepts both clew's wrapped form and
bare OTel streams.

```bash
clew otel-import trace.ndjson --branch from-collector
```

### `clew trace -- <cmd> [--name NAME] [--timeout SECONDS]`

Run a subprocess and record it as a single span.

```bash
clew trace --name my-agent -- python my_agent.py
```
