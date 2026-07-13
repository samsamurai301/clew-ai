# Sharing traces (signed bundles)

When you want to send a trace to a teammate — for debugging,
for review, for the "what would have happened" conversation —
`clew share` exports a portable signed bundle.

## Quick example

```bash
# 1. Generate a signing key (one-time)
clew keygen --out ~/.clew/key.pem
# private key: /home/you/.clew/key.pem  (keep this secret)
# public  key: /home/you/.clew/key.pem.pub

# 2. Export the trace
clew share <trace_id> --key ~/.clew/key.pem --out trace.tgz
# /path/to/trace.tgz

# 3. Send trace.tgz + the .pub file to your teammate.

# 4. They verify + import
clew verify trace.tgz --public-key clew-pub.pem
# valid  trace_id=...  spans=4  created_at=...

clew import trace.tgz --public-key clew-pub.pem --branch from-you
# imported 4/4 spans, trace_id=...
```

## Bundle format

A bundle is a `tar.gz` file with three top-level entries:

```
manifest.json   # bundle metadata (trace id, content hash, signer)
sig             # 64-byte Ed25519 signature over manifest.json
spans/<id>.json # one JSON file per span
```

The manifest declares:

| Field | Meaning |
|---|---|
| `format` | Always `clew-bundle`. |
| `version` | Bundle format version. Currently `1`. |
| `created_at` | RFC 3339 UTC timestamp of bundle creation. |
| `trace_id` | The id of the trace this bundle contains. |
| `span_count` | Number of spans. |
| `spans_sha256` | SHA-256 over all span JSON bytes, in order. |
| `source_store` | Path to the store the trace came from. |
| `public_key` | PEM-encoded Ed25519 public key of the signer. |

## What's signed

The 64-byte Ed25519 signature covers the **canonical bytes** of
the manifest (UTF-8 JSON, sorted keys, 2-space indent). It's
deterministic — two bundles with the same manifest produce the
same signature.

The spans_sha256 is **not** signed directly; it's used as a
defense-in-depth check. The full verification flow is:

1. **Ed25519 check**: the signature over the manifest is valid.
2. **Format check**: the manifest declares `format: clew-bundle`.
3. **Content check**: the SHA-256 of all span bytes matches
   `spans_sha256` in the manifest.

If any check fails, the bundle is rejected with a non-zero exit
code and an error message.

## Why Ed25519?

- **Fast.** ~70k signatures/sec on a modern CPU. Verification
  is faster than signing.
- **Small.** 64-byte signatures, 32-byte public keys. The
  bundle overhead is negligible.
- **Audited.** Ed25519 has been around since 2011 and has
  withstood extensive cryptanalysis. The `cryptography`
  library's implementation is the gold standard.
- **No padding oracle.** Ed25519 is deterministic, so signing
  the same message twice gives the same signature. There is
  no "valid vs invalid padding" side channel.

## Key management

The private key is written to disk in PKCS8 PEM format,
unencrypted. This is intentional — the user is expected to:

- Put the key in a password manager (1Password, Bitwarden).
- Or in `~/.clew/key.pem` with mode 0600.
- **Never** commit it to git. **Never** paste it in a chat.

The public key is meant to be shared. Put it in your team's
1Password, in a `TEAM_PUBKEY.pem` file in the repo (not the
private one), or pass it directly when sharing a bundle.

## Re-signing the same bundle

If you update a span in the source store and want to re-share,
just run `clew share` again. The bundle is rebuilt from
scratch; old bundles remain valid (they're immutable artifacts).

## Import into a fresh store

```bash
# On the recipient's machine:
clew init
clew import trace.tgz --public-key teammate-pub.pem --branch shared
```

The `--branch` option creates a new branch pointing at the
imported root. Without it, the spans are added to the store
but no branch is created — the trace is reachable only via
its content-addressed id, not as a HEAD.

## See also

- [Architecture: bundle format](../internals/bundle-format.md)
- [CLI reference: share, verify, import](../reference/cli.md#clew-share)
