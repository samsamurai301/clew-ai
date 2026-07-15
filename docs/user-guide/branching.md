# Branching and replay

A branch is a validated name pointing at one finalized span occurrence. It is an atomic
local ref, not a duplicate of trace data.

```bash
clew branch candidate SPAN_ID
clew checkout candidate
clew branches
```

Ref values are exactly 32 lowercase hexadecimal characters. The all-zero value is used only
for a fresh store's empty `main` placeholder. Missing, malformed, or corrupt targets fail
explicitly and are reported by `clew doctor`.

## Replay

```bash
clew replay TRACE_ID --executor mock
clew replay TRACE_ID \
  --executor my_package.replay:execute \
  --from SPAN_ID \
  --branch candidate
```

Partial replay clones the complete ancestor closure, executes the selected occurrence and
descendants, and rewrites every parent into a fresh trace. Multi-parent joins remain
multi-parent joins. The source trace is never mutated.

Executors accept `(Span, ReplayContext)` and return `ReplayResult`, either synchronously or
as an awaitable. See the [replay engine](../internals/replay.md).

## Diff

```bash
clew diff ORIGINAL_TRACE NEW_TRACE
clew diff ORIGINAL_TRACE NEW_TRACE --json
```

Alignment uses ancestry, type/name, and sibling occurrence order. Repeated siblings with
the same name remain separate comparisons.
