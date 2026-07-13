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
