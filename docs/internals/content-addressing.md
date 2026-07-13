# Content addressing

Every clew span id is a SHA-256 of the canonical-JSON serialization
of the span's *content*. Two spans with identical content have
identical ids, regardless of when or where they were created.
This page explains the algorithm and the trade-offs.

## The hash

A span's id is `sha256(canonical_json(span))`, where the
canonical-JSON serialization is:

1. Drop fields that are not content: `id`, `started_at`, `ended_at`.
   Two runs of the same code at different times should produce the
   same id.
2. Sort keys.
3. No whitespace.
4. UTF-8 encoded.

The actual implementation is in `clew/utils/hash.py:span_hash`.

## What is "content"?

The fields that contribute to the hash are:

- `trace_id` (the trace this span belongs to)
- `parent_ids` (its position in the DAG)
- `type` (LLM, TOOL, DECISION, OBSERVATION)
- `name` (the span's display name)
- `attributes` (structured key-value metadata)
- `input` (the span's input value, before execution)
- `output` (the span's output value, after execution)
- `status` (OK, ERROR, RUNNING)
- `error` (error message, if status is ERROR)
- `metadata` (free-form additional metadata)

Notice that `id` and the timestamps are *excluded* from the hash.
This is the content-addressing property: the id is *derived*
from the content, not *part* of it.

## The trace_id

The `trace_id` is the id of the trace's **root span**. Each
span belongs to exactly one trace; the trace id is the root
span's id.

For spans created via `@t.agent`, the trace id is a fresh UUID
(random) — two unrelated agent runs do not share a trace id.
For spans created via `@t.span`, the trace id is inherited
from the parent span (or a fresh UUID if there's no parent).

## Why content addressing?

Two reasons:

1. **Dedup.** When your agent retries the same call (a flaky
   network, a tool that re-executes), the second attempt's
   span collapses into the first. The store doesn't grow, the
   index doesn't double, and `clew log` doesn't show two
   rows.

2. **Branching.** When you replay a trace, ancestors that
   are bit-identical to the original share the same id. The
   diff engine matches by id *or* by path-from-root, so
   modified descendants land side-by-side in the diff view
   even when they have completely new content.

## Trade-offs

Content addressing is a great fit for clew, but it has
costs:

- **Span ids are not sequential.** You can't sort a trace
  by id and get execution order. clew sorts by
  `started_at` (or by topology when timestamps are missing).
- **Two semantically different spans with the same content
  collapse.** If your agent has two LLM calls with the same
  prompt and the same response, they share an id. This is
  almost always what you want (it's dedup), but be aware
  of it when debugging.
- **Renaming a span changes its id.** The span's `name` is
  part of the content. Renaming a function from `plan` to
  `make_plan` produces a different span id even if
  everything else is identical.

## Hash collision resistance

SHA-256 has 128-bit collision resistance, which is enough for
any realistic local clew store. A store with a billion spans
has a collision probability of roughly 10⁻²⁰ — small enough
that you'll never hit it.

## See also

- [Architecture](architecture.md) — the full file layout
- [Replay engine](replay.md) — how content addressing makes
  replay efficient
