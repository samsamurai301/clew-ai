# Sharing signed traces

```bash
clew keygen --out ~/.config/clew/signing-key.pem
clew share TRACE_ID \
  --key ~/.config/clew/signing-key.pem \
  --out trace.tgz
clew verify trace.tgz \
  --public-key ~/.config/clew/signing-key.pem.pub
```

The v2 archive contains `manifest.json`, `sig`, and one canonical
`spans/<32-hex-id>.json` member per occurrence. Verification checks the member allowlist,
size limits, Ed25519 signature, aggregate member digest, every record hash, trace identity,
sequence uniqueness, root, parents, and cycles.

Import into an initialized v2 store:

```bash
clew import trace.tgz --public-key trusted-key.pub --branch shared
```

Exact existing bytes are idempotent. A conflicting ID never overwrites the existing
record. Bundle v1 is rejected.

Bundles are not encrypted and a signing key is not a human identity. Protect the private
key, distribute trusted public keys separately, and encrypt sensitive bundles out of band.

See [Signed bundle format v2](../internals/bundle-format.md).
