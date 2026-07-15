# Occurrence identity and record integrity

Clew v2 separates **identity** from **integrity**.

## Identity

Each finalized span occurrence gets a fresh UUID4 hexadecimal `id`. Each independently
started execution gets a separate UUID4 hexadecimal `trace_id`. Both are exactly 32
lowercase hexadecimal characters.

Two calls with identical input and output remain two records. Two calls with identical
input and different output also remain two records. This is required for an execution
debugger: frequency, ordering, and nondeterministic results are evidence, not duplicates.

`sequence` is a unique non-negative integer within a trace. It provides deterministic
execution order independently of timestamp resolution.

## Integrity

After a span finishes, Clew canonicalizes every persisted field except `content_hash` and
computes a SHA-256 digest. The protected fields include identity, parent order, sequence,
type, name, attributes, input, output, timestamps, status, error, and metadata.

The store recalculates that digest on every read and write. Rewriting an existing ID with
the exact same bytes is idempotent. Reusing an existing ID with different bytes raises
`ConflictingSpanError`; the original record is not overwritten.

The hash detects accidental or malicious record changes. It is not a signature and does
not identify who wrote the record. Signed bundles add Ed25519 authentication for portable
traces.

## Finalization

`RUNNING` is internal transient state. The immutable public `Span` exists only after the
operation finishes and has one terminal status:

- `OK`: execution completed.
- `ERROR`: execution failed and includes an error message.
- `SKIPPED`: replay did not execute the span because a dependency failed.
