# Security policy

## Supported versions

Clew is currently Beta. Security fixes are provided for the latest published release.
The v1 store and bundle formats are not supported by Clew 1.1.5.

| Version | Status |
| --- | --- |
| 1.1.5 | Supported after publication |
| 1.1.4 | Unsafe launch artifact; yank requested |
| Earlier | Unsupported |

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/samsamurai301/clew-ai/security/advisories/new).
Do not open a public issue for an unpatched vulnerability or include secrets, private
traces, or working exploit details in a public discussion.

Private reporting must be enabled in the repository's **Settings → Security → Code
security and analysis** page before the 1.1.5 release. If that form is unavailable, do
not publish the report; open a content-free public issue asking the maintainer to enable
private reporting.

## Security boundary

Clew is a local debugger. It runs with the permissions of the current operating-system
user and stores prompt, tool, and model payloads as plaintext under `.clew/`.

- Anyone who can read the store can read the trace payloads.
- Anyone who can write the store can delete it or deny service. Clew detects record
  tampering; it cannot protect files from an account that controls the directory.
- The SQLite database is a rebuildable index. Canonical v2 JSON records and their
  verified `content_hash` values are the source of truth.
- No analytics or adoption telemetry is sent by the runtime.
- The optional MCP server communicates over stdio and trusts the process that launches
  it. It does not bind a network port or authenticate its parent process.
- Provider integrations call the provider selected by the application. Clew itself does
  not add analytics requests.

## Signed bundles

Bundle v2 uses Ed25519. A valid signature proves that the manifest was signed by the
holder of the matching private key and that the manifest binds the included record bytes.
It does not prove the signer's real-world identity, establish when the bundle was made,
or encrypt its contents.

Generate and store private keys outside the repository:

```bash
clew keygen --out ~/.config/clew/signing-key.pem
clew share TRACE_ID --key ~/.config/clew/signing-key.pem --out trace.tgz
clew verify trace.tgz --public-key trusted-public-key.pem
```

Encrypt bundles separately when sharing them over an untrusted channel.

## Implemented defenses

- Span and trace IDs are exactly 32 lowercase hexadecimal characters; traversal-shaped
  identifiers are rejected by the model and storage layers.
- Bundle members use an allowlist. Symlinks, hard links, device files, absolute paths,
  traversal, excessive member counts, and excessive uncompressed sizes are rejected.
- Record hashes are checked on every read and write. The same ID plus identical bytes is
  idempotent; the same ID plus different bytes is a corruption error.
- Store writes use cross-process locking, unique exclusive temporary files, `fsync`, and
  atomic replacement. SQLite uses WAL, a busy timeout, and a rebuildable index.
- Ref names and values are validated. Ref and HEAD updates are atomic, and symlinks are
  not followed while listing refs.
- Subprocess tracing passes an argument vector directly and never enables a shell.
- Persisted formats use JSON. Clew does not deserialize `pickle`, `marshal`, or unsafe
  YAML.
- The v2 reader refuses v1 or unversioned stores without modifying or deleting them.

## Operational guidance

- Do not commit `.clew/`, API keys, provider credentials, signing keys, or PyPI tokens.
- Run `clew doctor` after an interrupted write or before sharing a bundle.
- Treat imported OTel-shaped NDJSON and bundles as untrusted input.
- Use least-privilege provider keys and avoid recording credentials in agent payloads.
- Keep dependencies current and review Dependabot and CodeQL findings before release.
