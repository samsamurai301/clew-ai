# Quickstart

A 5-minute walkthrough of clew: install, trace, branch, replay, diff, share.

## 0. Install

```bash
# With uv (recommended)
uv add clew

# With pip
pip install clew
```

Requires Python 3.11+.

## 1. Initialize

```bash
mkdir my-project && cd my-project
clew init
```

This creates `.clew/` in the current directory with the store layout:

```
.clew/
├── manifest.json
├── HEAD              # current branch name
├── refs/             # branches
├── spans/            # content-addressed JSONL
└── index.sqlite      # queryable index
```

## 2. Trace your first agent

Save this as `agent.py`:

```python
from clew.sdk import Tracer, SpanType

t = Tracer()

@t.agent
def answer(question: str) -> str:
    @t.span("plan", type=SpanType.DECISION)
    def p() -> str:
        return question.split()[0]

    @t.span("answer", type=SpanType.LLM)
    def a(query: str) -> str:
        return f"You asked: {query}"

    return a(p())

answer("What is clew?")
```

Run it:

```bash
uv run python agent.py
```

## 3. Inspect

```bash
$ clew log
                              clew traces
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ trace id      ┃ root     ┃ spans ┃ started               ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ ea23b3f32bc1… │ answer   │     3 │ 2026-07-13T19:04:33…  │
└───────────────┴──────────┴───────┴──────────────────────┘
```

```bash
$ clew show ea23b3f32bc1
ea23b3f32bc1…  (3 spans)
└── answer [OBSERVATION] OK  (1.0ms)
    ├── plan [DECISION] OK  (0.0ms)
    └── answer [LLM] OK  (0.0ms)
```

## 4. Branch a reasoning trace

Get the id of the `plan` span:

```bash
$ clew show ea23b3f32bc1 --json | head -2
{"id":"abc...","name":"plan",...}
```

Create a branch at that span:

```bash
$ clew branch experiment abc...
Created branch 'experiment' → abc…
```

## 5. Replay under a different model

```bash
$ clew replay ea23b3f32bc1 --executor mock
<new-trace-id>
```

The original trace is unchanged; a fresh trace is created with the same logical structure.

## 6. Diff the two

```bash
$ clew diff ea23b3f32bc1 <new-trace-id>
╭───────────────────────────────── clew diff ──────────────────────────────────╮
│ --- ea23b3f32bc1                                                             │
│ +++ <new-trace-id>                                                           │
│ @@ 0 modified, +0 -0, 3 unchanged @@                                         │
╰─────────────────────────────────────────────────────────────────────────────╯
```

Both traces are identical (mock executor re-uses outputs). Try `python -m clew.examples.branching_demo` to see a real replay with a different model.

## 7. Share as a portable bundle

```bash
$ clew share ea23b3f32bc1
/path/to/ea23b3f32bc1…clew.tgz

$ tar tzf ea23b3f32bc1…clew.tgz
manifest.json
spans/...
```

The bundle includes a `manifest.json` (with the trace id, sha256 hash, and creation time) plus all span files. Send it to a colleague; they can extract it and reproduce your trace.

## 8. Launch the TUI

```bash
$ clew tui
```

A 3-pane terminal browser: traces on the left, span tree on the right, span details at the bottom. Press `q` to quit, `enter` to expand, `b` to branch, `r` to replay, `d` to diff.
