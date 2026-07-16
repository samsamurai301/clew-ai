# Persisted protocol v2

This page describes the only store and bundle formats supported by Clew 1.1.5.

## Store manifest

`.clew/manifest.json` is a JSON object containing:

```json
{
  "format": "clew-store",
  "version": 2,
  "created_at": "RFC 3339 timestamp"
}
```

An absent manifest beside v2 records is an error. A different version raises
`UnsupportedStoreVersion`. Opening an unsupported store does not create or change its
records, index, lock, HEAD, or refs.

## Span record

Each record lives at `.clew/spans/<id[:2]>/<id>.json` and is canonical JSON for one
immutable finalized `Span`:

```json
{
  "id": "32 lowercase UUID4 hex characters",
  "trace_id": "32 lowercase UUID4 hex characters",
  "parent_ids": [],
  "sequence": 0,
  "type": "OBSERVATION",
  "name": "agent",
  "attributes": {},
  "input": null,
  "output": null,
  "started_at": "2026-07-15T10:00:00Z",
  "ended_at": "2026-07-15T10:00:01Z",
  "status": "OK",
  "error": null,
  "metadata": null,
  "content_hash": "64 lowercase SHA-256 hex characters"
}
```

`type` is `LLM`, `TOOL`, `DECISION`, or `OBSERVATION`. Persisted `status` is `OK`,
`ERROR`, or `SKIPPED`. `ERROR` requires a non-empty error; the other statuses forbid one.

`content_hash` is SHA-256 over canonical JSON for every field above except
`content_hash`. Parent list order is significant.

## Trace invariants

All records in one trace share `trace_id`, have unique IDs and unique `sequence` values,
contain exactly one parentless root, reference only present same-trace parents, and form an
acyclic graph.

## Refs and HEAD

`.clew/refs/<name>` contains exactly one 32-character span ID plus a newline. The all-zero
ID is permitted only as the initial empty placeholder. `.clew/HEAD` contains a validated
branch name. Updates use unique temporary files and atomic replacement while the store's
cross-process lock is held.

## SQLite index

`index.sqlite` is a WAL-mode performance index, not authoritative data. Clew rebuilds a
missing or invalid index from verified JSON records. A unique `(trace_id, sequence)`
constraint detects ordering conflicts.

## Bundle v2

See [Signed bundle format v2](../internals/bundle-format.md). Bundle v1 is rejected.

## OTel-shaped NDJSON

NDJSON is an import/export projection, not the canonical store and not OTLP. Bulk import
allocates fresh Clew identities and rewrites topology. See
[OTel-shaped NDJSON](../integrations/otel.md).
