# clew — Protocol Specification

> **Version:** 0.1.0
> **Status:** Normative for clew v0.1.0. Backwards-incompatible changes bump
> the major version and require a migration path.

This document defines the **byte-level** format clew uses to store, hash,
and exchange reasoning traces. It is the contract every implementation
(SDK, CLI, TUI, third-party tool) must follow. The
[OpenTelemetry Generative AI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
are the source of truth for attribute names; clew honors them where they
apply and adds clew-specific fields where OTel is silent.

---

## 1. Trace file format (JSONL)

A clew trace is a stream of **spans**, one span per line, in
**chronological order of arrival** (the order the agent produced them, not
the order they were hashed). The canonical on-disk representation is
[JSON Lines](https://jsonlines.org/) — UTF-8, `'\n'`-delimited, no
trailing comma, no enclosing array.

```text
{"id":"ab12…","trace_id":"…","parent_ids":[],"type":"LLM", …}\n
{"id":"cd34…","trace_id":"…","parent_ids":["ab12…"],"type":"TOOL", …}\n
{"id":"ef56…","trace_id":"…","parent_ids":["ab12…"],"type":"LLM", …}\n
```

### 1.1 Why JSONL?

* **Streaming.** A running agent can `append` to the JSONL as it goes;
  no need to hold the trace in memory.
* **Tail-friendly.** `tail -f .clew/traces/<id>.jsonl | jq .name` is
  a real-time debugger.
* **Append-only.** Lines are never rewritten, never reordered. The
  filesystem is the log.
* **Grep-friendly.** `grep '"type":"TOOL"'` works without a parser.

### 1.2 File location

A trace lives at `.clew/traces/<trace_id>.jsonl` where `<trace_id>` is
the SHA-256 hex of the root span (so the directory is sharded and
human-meaningful: the file name *is* the trace).

### 1.3 Idempotency

Appending a span whose `id` already appears in the file is a **no-op**
— the writer must not duplicate the line. This makes
network-retry and process-restart safe.

---

## 2. Span schema

A span is a JSON object with the following fields. All fields are
**required** unless explicitly marked optional. Field order in the
serialized form is **sorted lexicographically by key** (see §3.1).

| Field | Type | Required | Description |
| --- | --- | :---: | --- |
| `id` | string (hex SHA-256, 64 chars) | ✅ | The content hash of the span (see §3) |
| `trace_id` | string (hex SHA-256, 64 chars) | ✅ | Hash of the root span of the trace |
| `parent_ids` | list of strings | ✅ | Span ids of the direct parents (empty for root) |
| `type` | string enum | ✅ | `"LLM"`, `"TOOL"`, `"DECISION"`, or `"OBSERVATION"` |
| `name` | string | ✅ | Human-readable label, ≤ 200 chars |
| `attributes` | object (string → JSON) | ✅ | OTel-style attributes; never `null` (use `{}`) |
| `input` | any JSON | ✅ | Request payload; opaque to clew |
| `output` | any JSON | ✅ | Response payload; opaque to clew |
| `started_at` | string (RFC 3339 UTC) | ✅ | Wall-clock start, e.g. `"2026-07-13T18:28:55.123Z"` |
| `ended_at` | string (RFC 3339 UTC) | ✅ | Wall-clock end, e.g. `"2026-07-13T18:28:56.789Z"` |
| `status` | string enum | ✅ | `"OK"`, `"ERROR"`, or `"RUNNING"` |
| `error` | string \| null | ❌ | Error message when `status == "ERROR"` |
| `metadata` | object (string → JSON) | ❌ | SDK version, host, model id, etc. |

### 2.1 Type rules

* `parent_ids` is a **list**, not a single id. A span may have zero
  parents (root), one parent (most spans), or multiple (a join/merge
  span that synthesizes two prior branches).
* `attributes` keys should use the OpenTelemetry dot-namespace convention
  (`gen_ai.*`, `http.*`, `db.*`, etc.). clew does not enforce this — it
  is a convention, not a schema — but tools are encouraged to honor it.
* `input` and `output` may be `null` for spans where they are not
  meaningful (e.g. an `OBSERVATION` recording a heartbeat).
* `started_at < ended_at` always. A `RUNNING` span's `ended_at` is the
  start time plus 1 nanosecond (or a sentinel `null` in transit, but
  always present and well-formed on disk).
* `error` is **only** populated when `status == "ERROR"`.

### 2.2 Why all fields are required (or explicitly optional)?

A span is a *value object*. The protocol deliberately rejects partial
spans and "patch" semantics. If a field is unknown, it is `null` or `{}`
or absent (per the table). This makes hashes stable and bundles
diffable.

---

## 3. Content addressing

A span's `id` is the **SHA-256 hex digest** of the **canonical-JSON
serialization of the span with the `id` field removed**.

### 3.1 Canonical JSON

The canonical encoder produces a byte sequence with these properties:

1. **Object keys are sorted** in lexicographic order of their UTF-8
   byte representation. No reordering by Unicode codepoint.
2. **No insignificant whitespace.** No spaces, no newlines, no
   indentation between tokens. Tokens are concatenated.
3. **Numbers** are serialized as JSON numbers (no NaN, no Infinity —
   clew rejects these at write time). Integer values with no fractional
   part are emitted without a decimal point.
4. **Strings** use double quotes; control characters and `"` are
   escaped per RFC 8259. UTF-8 output.
5. **Arrays** preserve order (order is semantic; do not sort).
6. **Booleans** are `true` and `false` (lowercase, no quotes).
7. **`null`** is emitted as the literal `null` (lowercase, no quotes).
8. **Object values** that are `undefined`/missing are not emitted (this
   is what allows `id` to be excluded for hashing and then filled in).
9. **Duplicate keys** are an error. (Pydantic guarantees no
   duplicates; manual writers must check.)
10. **Trailing newline**: none. Hashes are of the JSON value alone.

The Python reference implementation is `clew.utils.hash.canonical_json`
(owned by the `core-data` task); it must produce byte-for-byte
identical output to the spec.

### 3.2 The hashing algorithm

```python
# pseudocode — the reference impl lives in clew.utils.hash
def span_id(span: dict) -> str:
    span = dict(span)            # shallow copy
    span.pop("id", None)         # exclude the id field
    canonical = canonical_json(span)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest                # 64 lowercase hex chars
```

### 3.3 Why this works

* **Determinism.** Two implementations, two languages, two machines,
  same span → same id.
* **Tamper-evidence.** Edit one byte of a span file on disk, the id
  in the filename no longer matches, the loader raises.
* **Mergeability.** "Did this span change between runs?" reduces to
  "are the ids the same?"

### 3.4 Hashing the trace_id

The `trace_id` is the id of the **root span** of the trace. Once the
root span is hashed, all other spans in the trace inherit that
`trace_id` verbatim.

---

## 4. Append-only invariant

The store is **append-only** for both spans and refs.

### 4.1 Spans

* A span is written to `.clew/objects/span/<aa>/<rest>` exactly once.
* Writing the same span a second time is a no-op (idempotent).
* Writing a *different* span to the same path is **impossible** — the
  path *is* the hash, and a different content hashes to a different
  path.
* Span files are `chmod 0444` (read-only) after the writer flushes
  them, to make accidental mutation obvious.

### 4.2 Refs

* A ref (`.clew/refs/<category>/<name>`) is a single-line text file
  containing a span id.
* Ref updates are atomic: `write temp + os.rename`. A crash leaves
  either the old or the new value, never a half-written file.
* Every ref write is appended to `.clew/logs/HEAD.log` for audit.

### 4.3 JSONL traces

* The per-trace `.clew/traces/<id>.jsonl` is the **only** file that
  grows. Lines are appended; old lines are never edited or removed
  (a `clew gc` is the only thing that may remove a file, and it never
  removes a referenced span).

---

## 5. Branch refs

A branch is a file at `.clew/refs/heads/<name>`, containing one line
(the span id) and a trailing newline.

```text
$ cat .clew/refs/heads/main
ab12cd34ef567890ab12cd34ef567890ab12cd34ef567890ab12cd34ef567890
```

### 5.1 Categories of refs

| Path | Mutable? | Use |
| --- | :---: | --- |
| `refs/heads/<branch>` | yes | Active branches |
| `refs/tags/<tag>` | no (write-once) | Pointers to specific reasoning states |
| `refs/remotes/<remote>/<branch>` | yes | Remote-tracking refs (v0.2+) |
| `HEAD` | yes | The current ref; usually `ref: refs/heads/main` |

### 5.2 Naming rules

* Branch names are case-sensitive.
* Allowed characters: ASCII letters, digits, `-`, `_`, `/`, `.`.
* Reserved names: `HEAD`, `@` — these are meta-refs.
* Names containing `/` are stored as nested directories (e.g.
  `experiment/2026-07-mini` → `.clew/refs/heads/experiment/2026-07-mini`).

### 5.3 The HEAD file

`HEAD` is a 1–2 line text file:

```text
ref: refs/heads/main
```

or, in detached mode, a single line with a span id:

```text
ab12cd34ef567890ab12cd34ef567890ab12cd34ef567890ab12cd34ef567890
```

Detached HEAD is unusual but legal; it is what `clew checkout <span-id>`
produces.

---

## 6. Portable bundle

A portable bundle is a single file a developer can email, commit to a
repo, or upload anywhere. It is self-contained: opening it on another
machine with clew gives the user the full trace and ref graph.

### 6.1 Bundle structure

A bundle is a gzipped tar archive (`.clew.bundle`) with the following
layout:

```
my-trace.clew.bundle         # gzipped tar
└── bundle/
    ├── manifest.json        # bundle metadata (see §6.2)
    ├── signature.sig        # Ed25519 detached signature of manifest.json
    ├── .clew/               # the repository contents (objects, refs, traces)
    │   ├── objects/span/…
    │   ├── refs/heads/…
    │   └── traces/…
    └── README.md            # human-readable summary (auto-generated)
```

### 6.2 `manifest.json`

```json
{
  "clew_version": "0.1.0",
  "bundle_format": 1,
  "name": "my-trace",
  "created_at": "2026-07-13T18:28:55Z",
  "creator": {
    "name": "Alice Developer",
    "email": "[email protected]"
  },
  "trace_ids": ["ab12…", "cd34…"],
  "spans": 1247,
  "size_bytes": 3145728,
  "public_key": "ed25519:MCowBQYDK2VwAyEA…"
}
```

`bundle_format` is a monotonic integer; `clew` v0.1.0 reads format
`1`. Higher formats require a `clew` upgrade.

### 6.3 Signature

`signature.sig` is a 64-byte Ed25519 signature of the canonical JSON
serialization of `manifest.json` (the same canonical encoder from §3.1).
The signer's public key is embedded in `manifest.json.public_key`.
Verification command:

```bash
$ clew share-verify my-trace.clew.bundle
Signature:        OK
Signer:            ed25519:MCowBQYDK2VwAyEA…
Trust:             first-use (no key in trusted-keys)
Import?            [Y/n]
```

### 6.4 `clew share` and `clew share-open`

* `clew share` produces a bundle. It writes the tar, signs the
  manifest, and prints the path.
* `clew share-open <file>` verifies the signature, prompts for trust
  on a first-seen key, and extracts the `.clew/` into a new sibling
  directory or merges into an existing one (configurable).

---

## 7. OTel mapping

clew honors the
[OpenTelemetry Generative AI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
The mapping is best-effort: clew mirrors the OTel span tree and
projects a curated subset of OTel attributes into clew-native fields
(`name`, `input`, `output`, `status`, `error`).

### 7.1 Span-type mapping

| OTel `span.kind` / instrumentation | clew `SpanType` |
| --- | --- |
| `gen_ai.chat` (OpenAI, Anthropic, etc.) | `LLM` |
| Tool call (via `gen_ai.execute_tool` or OTel `SpanKind.CLIENT` for HTTP) | `TOOL` |
| A user-emitted "I decided X" event | `DECISION` |
| Anything else OTel-instrumented | `OBSERVATION` |

### 7.2 Attributes we honor

These OTel `gen_ai.*` attributes (from the GenAI semconv) are
**first-class** in clew and are projected into the canonical span:

| OTel attribute | clew field / use |
| --- | --- |
| `gen_ai.system` | `attributes["gen_ai.system"]` (e.g. `"openai"`) |
| `gen_ai.request.model` | `attributes["gen_ai.request.model"]` |
| `gen_ai.response.model` | `attributes["gen_ai.response.model"]` |
| `gen_ai.request.max_tokens` | `attributes["gen_ai.request.max_tokens"]` |
| `gen_ai.request.temperature` | `attributes["gen_ai.request.temperature"]` |
| `gen_ai.usage.input_tokens` | `attributes["gen_ai.usage.input_tokens"]` |
| `gen_ai.usage.output_tokens` | `attributes["gen_ai.usage.output_tokens"]` |
| `gen_ai.response.finish_reasons` | `attributes["gen_ai.response.finish_reasons"]` |
| `gen_ai.tool.name` (tool calls) | `attributes["gen_ai.tool.name"]` |
| `gen_ai.tool.call.id` | `attributes["gen_ai.tool.call.id"]` |
| `gen_ai.conversation.id` | `trace_id` (re-derivable) |
| `gen_ai.agent.name` | `attributes["gen_ai.agent.name"]` |
| `gen_ai.operation.name` | `name` (when present) |

### 7.3 What we do not honor

* **OTel `Resource` attributes** (e.g. `service.name`, `host.name`) go
  into the trace's `metadata` envelope, not per-span.
* **OTel `Events`** (the `span.add_event(...)` API) are flattened into
  `attributes["events"]` as a list of `{name, time, attributes}` dicts.
  clew does not model events as first-class DAG nodes in v0.1.0.
* **OTel `Links`** (cross-trace links) are stored as
  `attributes["otel.links"]` and are not yet used for navigation.
* **OTel `SpanContext` (trace_id, span_id, trace_flags)** is mapped
  to clew's `trace_id` and `parent_ids`; OTel's 16-byte ids are
  re-hashed into 32-byte SHA-256 ids.

### 7.4 Round-trip

A span produced by an OpenTelemetry-instrumented library and ingested
via `clew.sdk.otel.OTelBridge` can be exported as a bundle and
re-ingested on another machine; the OTel attributes survive the
round-trip byte-for-byte.

---

## 8. Example spans

Two real-looking spans, exactly as they would appear in a JSONL trace
file (line breaks added here for readability — in the file each is
one line).

### 8.1 OpenAI chat completion (LLM span)

```json
{
  "id": "7f3a2c1b9d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a",
  "trace_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
  "parent_ids": ["11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"],
  "type": "LLM",
  "name": "plan_step_1",
  "attributes": {
    "gen_ai.system": "openai",
    "gen_ai.request.model": "gpt-4o-2024-08-06",
    "gen_ai.request.temperature": 0.2,
    "gen_ai.request.max_tokens": 1024,
    "gen_ai.response.model": "gpt-4o-2024-08-06",
    "gen_ai.response.finish_reasons": ["tool_calls"],
    "gen_ai.usage.input_tokens": 612,
    "gen_ai.usage.output_tokens": 87,
    "gen_ai.agent.name": "research_assistant"
  },
  "input": {
    "messages": [
      {"role": "system", "content": "You are a research assistant. Use tools when helpful."},
      {"role": "user", "content": "What is the population of Iceland?"}
    ],
    "tools": [
      {"type": "function", "function": {"name": "search_web", "description": "Search the web"}}
    ]
  },
  "output": {
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_AbCdEf123",
          "type": "function",
          "function": {
            "name": "search_web",
            "arguments": "{\"query\": \"population of Iceland 2026\"}"
          }
        }
      ]
    }
  },
  "started_at": "2026-07-13T18:28:55.123Z",
  "ended_at": "2026-07-13T18:28:56.789Z",
  "status": "OK",
  "error": null,
  "metadata": {
    "clew.sdk.version": "0.1.0",
    "clew.sdk.host": "alice-mbp.local",
    "gen_ai.request.model": "gpt-4o-2024-08-06"
  }
}
```

### 8.2 Tool call (TOOL span)

```json
{
  "id": "2c4d6e8f0a1b3c5d7e9f1a3b5c7d9e1f3a5b7c9d1e3f5a7b9c1d3e5f7a9b1c3d",
  "trace_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
  "parent_ids": ["7f3a2c1b9d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a"],
  "type": "TOOL",
  "name": "search_web",
  "attributes": {
    "gen_ai.tool.name": "search_web",
    "gen_ai.tool.call.id": "call_AbCdEf123",
    "http.request.method": "GET",
    "url.full": "https://duckduckgo.com/html/?q=population+of+Iceland+2026"
  },
  "input": {
    "query": "population of Iceland 2026",
    "max_results": 5
  },
  "output": {
    "results": [
      {"title": "Iceland — Population", "url": "https://www.worldometers.info/world-population/iceland-population/", "snippet": "Iceland 2026 population is estimated at 399,182 people."},
      {"title": "Statistics Iceland", "url": "https://statice.is/", "snippet": "Quarterly population statistics for Iceland."}
    ]
  },
  "started_at": "2026-07-13T18:28:56.812Z",
  "ended_at": "2026-07-13T18:28:57.440Z",
  "status": "OK",
  "error": null,
  "metadata": {
    "clew.sdk.version": "0.1.0"
  }
}
```

---

## 9. Versioning & stability

* The protocol version is the clew version (`0.1.0` here).
* `bundle_format: 1` is the only format clew v0.1.0 reads and writes.
* `clew share-open` from a `bundle_format > 1` bundle fails with a
  clear upgrade error.
* `clew share-open` from a `bundle_format < 1` bundle is rejected.

The clew team commits to:

* New **optional** fields may be added to `Span` in minor versions
  (0.x). The store tolerates unknown fields on read (`extra="ignore"`
  on input).
* Removing or renaming a **required** field bumps the major version
  and ships with a migration tool.

---

*End of protocol specification. See `ARCHITECTURE.md` for the rationale
behind these choices, and `src/clew/core/models.py` for the
canonical Pydantic models.*
