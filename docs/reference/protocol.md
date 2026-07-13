# Protocol

The clew protocol defines the file formats, bundle format, and
store layout that every clew implementation must agree on. This
page is the canonical reference; if anything else disagrees with
this page, this page is right.

## On-disk store

A clew store is a directory of the following shape:

```
.clew/
├── spans/<aa>/<id>.jsonl  # one shard per span (content-addressed)
├── index.sqlite           # queryable index, rebuildable from JSONL
├── refs/<name>            # named pointers to span ids (one per line)
├── HEAD                    # current branch name
└── manifest.json           # store metadata
```

### `spans/<aa>/<id>.jsonl`

A single span is serialized as a JSON object (no trailing
whitespace, one per line):

```json
{
  "id": "ca29377a80f1...",
  "trace_id": "...",
  "parent_ids": ["..."],
  "type": "OBSERVATION",
  "name": "plan",
  "attributes": {"model": "gpt-4o"},
  "input": {"q": "..."},
  "output": "...",
  "started_at": "2024-01-01T00:00:00+00:00",
  "ended_at": "2024-01-01T00:00:01+00:00",
  "status": "OK",
  "error": null,
  "metadata": null
}
```

The id is the SHA-256 of the canonical-JSON serialization of
the span *with the `id` field set to an empty string*. See
[Content addressing](../internals/content-addressing.md) for
the algorithm.

### `index.sqlite`

A rebuildable SQLite index. The schema is:

```sql
CREATE TABLE spans (
  id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  started_at REAL,
  ended_at REAL,
  status TEXT NOT NULL,
  parent_ids TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL
);
CREATE INDEX idx_spans_trace_id ON spans(trace_id);
```

`parent_ids` is a JSON-encoded list. `started_at` and
`ended_at` are epoch seconds (REAL, millisecond precision).
`content_hash` is the result of
`clew.utils.hash.span_hash(span)`.

If the index is missing or corrupt, deleting `index.sqlite`
and reopening the store triggers a rebuild from the JSONL
files.

### `refs/<name>`

A text file containing one line: the span id the branch points
at. 64 hex characters, no prefix, trailing newline optional.

The placeholder ref is `0` * 64 (64 zeros) — this is the
default value for a freshly-initialized branch.

### `HEAD`

A text file containing one line: the name of the currently
checked-out branch.

### `manifest.json`

A JSON object. Schema:

```json
{
  "version": 1,
  "created_at": "2024-01-01T00:00:00.000+00:00"
}
```

`version` is the clew on-disk format version. Currently 1.
`created_at` is the time the store was initialized.

## Bundle format

A bundle is a tar.gz containing a single signed trace. See
[Bundle format (v1)](../internals/bundle-format.md) for the
full spec.

## OTel NDJSON

A trace exported via `clew export` is one JSON object per line:
a leading `_kind: trace` header followed by every span in OTel
shape. See the [OTel integration guide](../integrations/otel.md)
for the mapping details.

## MCP protocol

`clew mcp` exposes the store over the [Model Context Protocol](https://modelcontextprotocol.io).
12 tools + 2 resources; see the
[MCP integration guide](../integrations/mcp.md).

## Versioning

- **Store format** follows semver: a 1.x.y release of clew
  always reads a 1.x.z store. Future majors (2.0.0) will
  include a migration tool.
- **Bundle format** follows semver: a 1.x.y release of clew
  always reads a 1.x.z bundle. The bundle's `version` field
  tracks the format.
- **CLI and Python API** are *not* part of the protocol —
  they can change between any release (though we try to keep
  them stable within a major version).

## See also

- [Architecture](../internals/architecture.md) — the high-level design
- [Content addressing](../internals/content-addressing.md) — how ids are computed
- [Bundle format](../internals/bundle-format.md) — the v1 bundle spec
