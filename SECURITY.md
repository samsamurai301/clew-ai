# Security policy

## Supported versions

`clew` follows semver. Security fixes are backported to the
**latest minor** in the **1.x** series for at least 6 months
after release. Earlier 1.x.y releases get fixes on a best-effort
basis.

| Version | Supported          |
|---------|--------------------|
| 1.1.x   | ✅ actively        |
| 1.0.x   | ⚠️ best-effort     |
| < 1.0   | ❌ end-of-life     |

## Reporting a vulnerability

Email `security@clew.dev` (PGP key on request). Please do **not**
open a public GitHub issue for security reports.

We aim to acknowledge new reports within **3 business days** and
ship a fix within **30 days** of confirmation. Critical issues
(signed-bundle bypass, arbitrary code execution, path traversal
to a sensitive file) are patched within **7 days**.

## Threat model

`clew` is a **local-first**, **content-addressed**, **git-style**
debugger. Its security boundary is the user account that runs
`clew` and the files the user can read or write:

- The `.clew/` directory holds spans, refs, and a SQLite index.
  Anyone with read access to the directory can read all spans.
- The bundle format is signed with **Ed25519** for *integrity* and
  *authenticity*: a bundle signed with key K can only have been
  produced by a holder of the corresponding private key. Signing
  does **not** prove *when* the bundle was produced or *who* the
  holder is; for real identity, layer an X.509 / Sigstore / PGP
  wrapper on top.
- A bundle is **not encrypted** at rest. If you need to share
  spans over an untrusted channel, encrypt the bundle out-of-band
  (e.g. with `age`, `gpg`, or `ssh-keygen`-derived keys).
- The clew MCP server runs over stdio in the user's own process.
  It does not bind to a network socket, so the only attack
  surface is the local MCP client (Claude Desktop, Cursor, Cline).

## Hardening checklist

- Generate a fresh keypair for each identity that produces
  bundles. ``clew keygen --out <path>`` writes an **unencrypted**
  private key — store it in a password manager or hardware token,
  never in git.
- Verify bundles with a public key you trust:
  ``clew verify bundle.tar.gz --public-key trusted.pub``.
- Use ``clew doctor`` to detect store corruption after a crash or
  power loss.
- Use ``clew gc`` to remove span files that are no longer
  reachable from any branch.

## What clew does to protect you

- **Path traversal**: span ids and branch names are validated to
  be lowercase hex (8-64 chars) and `[A-Za-z0-9_.-]` respectively;
  any other character is rejected before it can reach the
  filesystem layer.
- **tarfile extraction** (CVE-2025-4138/4330/4517/7774 family):
  bundle extraction uses a strict allowlist of member names and
  refuses symlinks, hard links, device files, absolute paths, and
  `..` traversal. Decompressed size is capped at 256MB and member
  count at 1M by default; both can be overridden.
- **Insecure deserialization**: clew never uses ``pickle``,
  ``marshal``, ``shelve``, or ``yaml.load`` (unsafe variants). All
  on-disk data is JSON; all cross-process transport (MCP, OTel
  NDJSON) is JSON. The only cryptographic op is Ed25519 verify,
  via the `cryptography` library's verified APIs.
- **Subprocess invocation** (``clew trace --``): the command is
  passed as ``argv`` (a list of strings), never through a shell.
  Shell metacharacters are not interpreted.
- **Symlink following** in `refs/`: ``BranchManager.list()`` and
  ``clew branches`` skip any file that is a symlink, preventing
  a malicious ref from pointing outside the store.
- **HEAD validation**: ``BranchManager.current()`` validates the
  contents of ``HEAD`` against the same rules as a ref name. A
  poisoned ``HEAD`` (e.g. a CRLF in the name) raises
  ``ValueError`` rather than silently mis-directing the user.

## Known issues

- Private keys are written unencrypted. The threat model says so,
  but consider a wrapper script that uses an HSM / smart card.
- The MCP server does not authenticate the calling process; it
  trusts whatever's on the same stdin/stdout. This is the MCP
  design — clew follows the spec.
- The OTel bridge accepts a shared `tracer=` argument and does
  not validate span names. If you pass user-controlled names
  through an LLM, sanitize them.

## Credits

Reports that improve clew's security posture are credited (with
your permission) in the release notes.
