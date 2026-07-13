# Bundle format (v1)

A clew bundle is a portable tar.gz containing a single signed
trace. The format is stable; bundles created by any 1.x.y
release of clew can be read by any later 1.x.y release.

## Layout

```
manifest.json   # bundle metadata (see below)
sig             # 64-byte Ed25519 signature over manifest.json
spans/<id>.json # one JSON file per span
```

The `sig` and `spans/` are at the top level (not inside a
versioned subdirectory). clew 1.x.y always reads the layout
above; if we ever change it, the bundle's `version` field
will bump and the on-disk layout will diverge.

## manifest.json

```json
{
  "format": "clew-bundle",
  "version": 1,
  "created_at": "2024-01-01T00:00:00.000+00:00",
  "trace_id": "...",
  "root_span_id": "...",
  "span_count": 4,
  "spans_sha256": "<hex>",
  "source_store": "/path/to/.clew",
  "public_key": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `format` | string | yes | Always `clew-bundle`. |
| `version` | integer | yes | Bundle format version. Currently 1. |
| `created_at` | string | yes | RFC 3339 UTC timestamp of bundle creation. |
| `trace_id` | string | yes | The id of the trace this bundle contains. |
| `root_span_id` | string | yes | The id of the trace's root span. |
| `span_count` | integer | yes | Number of spans in the bundle. |
| `spans_sha256` | string | yes | SHA-256 over all span bytes, in order. |
| `source_store` | string | no | Path to the source store (informational). |
| `public_key` | string | yes | PEM-encoded Ed25519 public key. |

## Signature

The 64-byte `sig` file is an Ed25519 signature over the
**canonical bytes** of the manifest (UTF-8 JSON, sorted keys,
2-space indent). The bytes are the *exact* form that gets
written to disk and that gets read back for verification.

Signatures are deterministic (Ed25519 has no randomness),
so the same manifest always produces the same signature.
This makes the bundle byte-stable for archival purposes.

## Verification

`clew verify <bundle> --public-key <key>` does three checks
in order:

1. **Ed25519 check**: the signature over the manifest is
   valid. If this fails, the bundle was tampered with.
2. **Format check**: the manifest declares `format:
   clew-bundle`. If this fails, the file is not a clew
   bundle at all (perhaps it's a different tool's format).
3. **Content check**: the SHA-256 of all span bytes matches
   `spans_sha256` in the manifest. If this fails, the spans
   were modified after the bundle was signed.

If any check fails, `clew verify` exits 1 and prints the
specific reason.

## What is NOT in the bundle

- **Refs.** A bundle is a single trace. Refs (branch
  pointers) are not bundled; on import, you specify a
  branch name to create.
- **The SQLite index.** The bundle contains the raw span
  files. The index is rebuilt on the recipient's machine.
- **Other traces.** A bundle is a single trace; you can't
  bundle a branch with multiple traces. Run `clew share`
  once per trace.

## See also

- [Architecture](architecture.md) — the local store layout
- [Sharing](../user-guide/sharing.md) — the user-facing
  command
