# Searching: `clew query`

`clew query` searches spans across the store by any combination
of filters. Use it when you know roughly what you're looking
for but not which trace contains it.

## Filters

| Filter | Matches |
|---|---|
| `--name SUBSTR` | Case-insensitive substring of `span.name` |
| `--type TYPE` | Exact match on `SpanType`: `LLM`, `TOOL`, `DECISION`, `OBSERVATION` |
| `--status STATUS` | Exact match on `SpanStatus`: `OK`, `ERROR` |
| `--trace TRACE_ID` | Restrict to a single trace |
| `--metadata k=v` | Match `span.metadata[k] == v`. Repeatable, all keys must match. |
| `--limit N` | Cap the result count (default 50) |

All filters are AND-combined. The cheapest filter (status, type)
is applied at the SQLite index; the more expensive ones
(name substring, metadata match) are applied at scan time.

## Examples

```bash
# Every LLM call that errored
clew query --type LLM --status ERROR

# Every span mentioning gpt-4o
clew query --name gpt-4o

# Every tool call in a specific trace
clew query --type TOOL --trace 6f8a3b...

# Every LLM call where the model is gpt-4o and temperature was 0.7
clew query --type LLM --metadata model=gpt-4o --metadata temperature=0.7

# First 100 matches (default limit is 50)
clew query --type LLM --limit 100
```

## Output

### Text

```
                              span  type       status  name                  trace
  ca29377a80f1                LLM    OK        gpt-4o-call            6f8a3b...
  8d4c1e2f3a9b                TOOL   ERROR     search-tool            6f8a3b...
  ...
```

ERROR spans are highlighted in red; OK spans in green.

### JSON

```bash
clew query --json
```

```json
{
  "count": 2,
  "matches": [
    {
      "span_id": "ca29377a80f1...",
      "trace_id": "6f8a3b...",
      "root_span_id": "...",
      "type": "LLM",
      "name": "gpt-4o-call",
      "status": "OK",
      "started_at": "2024-01-01T00:00:00+00:00",
      "ended_at": "2024-01-01T00:00:01+00:00"
    }
  ]
}
```

Pipe to `jq`:

```bash
clew query --type LLM --status ERROR --json | jq '.matches[].name'
```

## Metadata values

`--metadata k=v` parses `v` as JSON if possible:

| Spec | Parsed as |
|---|---|
| `--metadata n=3` | `{"n": 3}` (int) |
| `--metadata ok=true` | `{"ok": true}` (bool) |
| `--metadata x=null` | `{"x": null}` (null) |
| `--metadata s="hello"` | `{"s": "hello"}` (string with quotes) |
| `--metadata s=hello` | `{"s": "hello"}` (no quotes, no JSON parse) |

So `--metadata n=3` matches spans where `metadata["n"] == 3`
even if the stored value is a string `"3"` (and vice versa).

## See also

- [CLI: inspect and query](cli.md#inspect)
- [Doctor & GC](doctor.md) — find *problems*, not spans
