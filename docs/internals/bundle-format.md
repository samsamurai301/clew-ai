# Signed bundle format v2

A Clew bundle is a gzip-compressed tar archive containing one trace:

```text
manifest.json
sig
spans/<32-hex-span-id>.json
...
```

Only bundle version 2 is accepted by Clew 1.1.5.

## Manifest

The canonical JSON manifest declares at least:

```json
{
  "format": "clew-bundle",
  "version": 2,
  "trace_id": "32 lowercase hexadecimal characters",
  "root_span_id": "32 lowercase hexadecimal characters",
  "span_count": 3,
  "spans_sha256": "64 lowercase hexadecimal characters"
}
```

`spans_sha256` binds the sorted member ID and exact JSON bytes for every span. Each JSON
record also verifies its own `content_hash`. `sig` is the Ed25519 signature over the exact
manifest bytes.

Verification bounds the entire decompressed tar stream before parsing, then checks the
member allowlist and payload/count limits before signature work, verifies
the signature, validates the v2 manifest, binds every member byte, validates each `Span`,
and checks trace identity, root, unique sequence values, complete parents, and cycles.

## Security properties

A valid bundle demonstrates integrity and possession of the matching signing key. It is
not encrypted, does not establish a timestamp, and does not map a key to a human identity.

The reader rejects traversal, absolute paths, symlinks, hard links, device files, FIFOs,
unexpected members, oversized uncompressed content or extension metadata, and excessive
member counts.

## Commands

```bash
clew keygen --out signing-key.pem
clew share TRACE --key signing-key.pem --out trace.tgz
clew verify trace.tgz --public-key signing-key.pub
clew import trace.tgz --public-key signing-key.pub
```

Import never overwrites a conflicting record. An exact existing record is idempotent; the
same ID with different content fails explicitly. The CLI persists the exact in-memory
span objects produced during signature verification; it does not reopen the pathname
between verification and persistence. The low-level `extract_spans` helper applies archive
safety checks but does not authenticate a signature by itself.
