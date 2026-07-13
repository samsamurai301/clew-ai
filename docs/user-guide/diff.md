# Diffing traces

`clew diff` compares two traces and shows what changed. The
default output is a colored text diff (similar to `git diff`),
and `--json` emits a structured document for tooling.

## How spans are matched

clew's diff engine does **not** match spans by id. Two runs
of the same code with the same inputs produce the same span
ids (content addressing), so a naive id-based diff would
report "everything is the same" — even when the user changed
the model and the answer is different.

Instead, clew matches spans by their **path from the root**:
the concatenation of `span.name` along the parent chain. So
`run > answer` matches another span at `run > answer`, even
if the underlying span ids are completely different.

For sibling spans with the same path, clew uses the order in
which they appear under their parent to disambiguate.

## What the diff shows

- **`+` added** — the span exists in `b` but not in `a`. This
  happens when a branch introduces a new step.
- **`-` removed** — the span exists in `a` but not in `b`. The
  reverse: a step that the branch removed.
- **`~` modified** — the span exists in both, but its content
  differs. The diff shows the field-level changes (input,
  output, attributes, status, error).
- **unchanged** — the span exists in both with the same content.
  Listed in the summary count but not in the body.

## What the diff matches on

The diff compares the *output* and *attributes* of matched
spans. Timestamps (`started_at`, `ended_at`) and `parent_ids`
are intentionally excluded — two replays of the same code
shouldn't show as "modified" just because they ran at
different times or with different parent ids.

## JSON output

```bash
clew diff <a> <b> --json
```

Returns a JSON document of the form:

```json
{
  "trace_id_a": "...",
  "trace_id_b": "...",
  "added": ["span_ids..."],
  "removed": ["span_ids..."],
  "modified": [
    {
      "path": "run/answer",
      "span_id_a": "...",
      "span_id_b": "...",
      "changes": [
        {"field": "output", "a": "old answer", "b": "new answer"},
        {"field": "attributes.model", "a": "gpt-4o", "b": "gpt-4o-mini"}
      ]
    }
  ],
  "unchanged_count": 1
}
```

## Text output

```
--- <trace_a_short>
+++ <trace_b_short>
@@ 3 modified, +0 -0, 1 unchanged @@
~ run/answer
  input:   "what is clew?"
  output:  "clew is a debugger" → "clew is a tool"
  status:  OK → OK
~ run/plan
  ...
  search
  (unchanged)
```

## Common uses

- **Model comparison** — branch, re-run with the new model,
  diff the two traces. The structural diff highlights exactly
  where the new model produced different output.
- **Prompt iteration** — change one prompt template, replay,
  diff. See which downstream steps changed.
- **Bug fix verification** — capture the bug, fix the code,
  replay, diff. If the modified spans disappear from the
  "modified" list, the fix worked.
- **Regression catching** — run your eval suite, diff each
  trace against a golden. Unexpected changes = regressions.

## Limitations

- The diff is per-span, not per-token. If two spans have the
  same output but different `attributes.metadata`, the diff
  shows the metadata change.
- Path matching is by `span.name`, not by span id. If you
  rename a span between two runs, the diff won't match them.
- Output is compared as opaque strings (no JSON-aware diff).
  For structured output, use `--json` and post-process.

## See also

- [Branching & replay](branching.md)
- [Internals: replay engine](../internals/replay.md)
