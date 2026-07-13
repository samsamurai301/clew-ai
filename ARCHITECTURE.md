# clew — Architecture

> **Tagline:** *git for AI reasoning.*
> **Version:** 0.1.0 (MVP)
> **Status:** Foundation document — defines the shape of the system.

---

## 1. Problem statement

Modern AI agents are not single function calls. They are **loops**: an LLM produces
text, the agent parses that text into tool calls, the tools run and return
observations, the LLM is invoked again, and the cycle continues until a stop
condition. A single user prompt can cause **dozens of LLM calls, hundreds of
tool calls, and thousands of tokens of intermediate reasoning** before an
answer is produced.

When something goes wrong — a hallucinated tool call, a loop, a wrong answer,
a runaway cost — the developer has almost no good way to investigate. They
get back the *final* string the agent emitted and a stack trace, but the
**reasoning path** that produced it is invisible.

The current generation of observability tools (LangSmith, Arize Phoenix,
Langfuse, Helicone, agentlens, etc.) treat agent runs as **telemetry**: they
record spans, latency, token counts, costs, and let you search and filter
them. That is genuinely useful for *metrics*, but it is the wrong abstraction
for *debugging*. Telemetry tells you *that* a call happened; it does not
let you ask:

* "What would have happened if at step 7 I had picked the second tool instead
  of the first?"
* "Replay this exact trace, but with `gpt-4o` swapped for `gpt-4o-mini`."
* "Show me the diff between run A and run B side by side, span by span."
* "Branch from this run, change the system prompt, and run only the tail."
* "Ship me a single signed file a colleague can open and replay locally."

These are *git* questions. They are version-control questions. The
**content of an agent run is a Merkle tree of reasoning steps**, and the
industry has been using spreadsheets to manage a problem that
[git](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects) solved for
source code in 2005.

clew exists to apply git's core insights — **content-addressing,
append-only history, branching, and portable bundles** — to **AI reasoning
traces**. Not as a metaphor, but as a literal data model.

---

## 2. The clew insight

Three ideas from git, lifted into the AI-reasoning domain:

### 2.1 Content-addressed spans

Every span (one LLM call, one tool call, one decision) is serialized to
**deterministic canonical JSON** and hashed with **SHA-256**. The hash *is*
the identifier. Two spans with identical content have identical ids,
everywhere, in every clew repository on Earth. This gives us:

* **Deduplication for free.** If two agents make the same call, the bytes
  on disk are one.
* **Integrity for free.** A signed bundle's spans are individually
  tamper-evident.
* **Merge for free.** A span has no name conflict with any other span.

### 2.2 Append-only Merkle DAG of spans

A trace is not a list — it is a **directed acyclic graph** of spans rooted
at an entry span. Each span references its parents by id. Adding a child
span never modifies any existing span (it is frozen, content-addressed).
This is the same property git's object database has for commits, and it
unlocks:

* **Branching at any span.** Create a new child of an existing span, give
  the head a name, and you have a branch. The original is untouched.
* **Replay from any point.** Starting from any span, you can re-execute
  the suffix against new inputs / new models without re-running the past.
* **Diffing two runs.** Two runs from the same ancestor share the prefix
  (same content-addressed spans) and diverge where the children differ.
  This is structural diff, not text diff.

### 2.3 Portable, signed bundles

A `.clew/` directory is just files. We tarball it, sign it (Ed25519
detached signature), and ship a single file. Anyone with clew installed
can `clew share-open trace.clew.bundle` and get a fully replayable trace.
No server, no account, no SaaS.

---

## 3. Data model

The data model is intentionally small. Four entities, all frozen Pydantic
v2 models in `src/clew/core/models.py`. See `PROTOCOL.md` for byte-level
serialization rules.

### 3.1 `Span` — a single reasoning step

A span is the atomic unit. A span can be:

* `LLM` — a chat completion (input messages, output message, model, tokens)
* `TOOL` — a tool invocation (name, arguments, result, latency)
* `DECISION` — an explicit branching decision (the agent chose branch A
  instead of B; the user can ask "what if I had chosen B?")
* `OBSERVATION` — an external event worth recording (a user message, a
  retrieval hit, a system note)

Every span carries:

* `id` — the SHA-256 hex of the canonical-JSON serialization of itself
  (without the id field). Self-referential but cheap: hash without → set
  id.
* `trace_id` — the id of the trace this span belongs to
* `parent_ids` — ordered list of parent span ids (empty for root, multiple
  for merge/join spans)
* `type` — `SpanType` enum
* `name` — human-readable label (`"plan"`, `"search_web"`, `"summarize"`)
* `attributes` — free-form key→JSON dict (OTel-style `gen_ai.*` keys
  encouraged)
* `input` / `output` — the request and response payloads, opaque JSON
* `started_at` / `ended_at` — RFC 3339 / ISO 8601 UTC timestamps
* `status` — `OK`, `ERROR`, or `RUNNING`
* `error` — optional error message (when `status == ERROR`)
* `metadata` — version, SDK version, model identifier, anything else

A span is **frozen** and **append-only**: once written, its content never
changes. To "edit" a span you write a new span with the new content (which
gets a new id) and create a branch pointing at it.

### 3.2 `Trace` — a Merkle DAG of spans

A `Trace` is a *projection* over a set of spans that share a `trace_id`.
The canonical Trace object holds:

* `trace_id` — string
* `root_span_id` — the entry span (no parents in this trace)
* `spans` — list of all spans in the trace, in insertion order

A trace is **discoverable** from any one of its spans (hash the span, look
up by `trace_id`). The list-of-spans view is a convenience for the TUI
and the SDK; the source of truth is the per-span files on disk.

### 3.3 `Branch` — a named pointer to a span

A `Branch` is what the user creates with `clew branch try-mini main`.
Internally it is a `Ref`, but it carries provenance:

* `name` — branch name (e.g. `"main"`, `"try-mini"`, `"experiment-2026-07"`)
* `head_span_id` — the span at the tip of this branch
* `created_at` — when this branch was created

The branch and the span it points to are separate concerns: a branch can
be moved (`clew checkout`) without changing the underlying span. The span
itself is immutable.

### 3.4 `Ref` — a generic named pointer

* `name` — ref name (e.g. `"main"`, `"HEAD"`, `"v0.1.0-rc1"`)
* `span_id` — the span this ref resolves to
* `updated_at` — when this ref was last written

`Branch` is sugar over `Ref` (branch = a ref that moves on `commit` and
must point to a `DECISION` or branchable span). `Ref` is the storage
primitive (literally a one-line file under `.clew/refs/<name>`).

### 3.5 Identity & canonicalization

Two spans are the same span iff their content-addressed ids match. To
compute an id:

1. Serialize the span to JSON with the **canonical** encoder (sorted keys,
   no insignificant whitespace, UTF-8, no NaN, no Infinity, no
   duplicate keys).
2. Drop the `id` field from the serialized form.
3. SHA-256 the resulting bytes.
4. Hex-encode.

This is deterministic across platforms, languages, and SDK versions.
Spec lives in `PROTOCOL.md §3`.

---

## 4. Storage layout

A clew repository is a single directory tree rooted at `.clew/` (located
at the project root, sibling to `.git/`). All clew operations are file
operations on this tree; no database server, no background process, no
network call.

```
.clew/
├── HEAD                       # the current ref (usually "ref: refs/heads/main")
├── config.toml                # user/project config (model aliases, defaults)
├── index.sqlite               # optional queryable index (spans by type/trace/time)
├── objects/
│   └── span/
│       └── ab/
│         └── cdef1234…        # one file per span; first 2 chars = dir, rest = name
├── refs/
│   ├── heads/<name>           # branch heads (one file per branch, contents = span_id)
│   ├── tags/<name>            # immutable tag pointers
│   └── remotes/<name>/<name>  # remote-tracking refs (v0.2+)
├── traces/
│   └── <trace_id>.jsonl       # append-only log of spans for a trace (chronological)
├── bundles/                   # exported portable bundles (.clew.bundle)
├── locks/                     # per-process file locks for concurrent writers
└── logs/
    └── HEAD.log               # ref-update log (audit trail)
```

### 4.1 Why this shape?

* **Per-span files (`objects/span/<aa>/<bbccdd…>`)** — O(1) lookup by id,
  perfect deduplication, identical to git's object store.
* **Append-only traces (`traces/<trace_id>.jsonl`)** — one line per
  span, in arrival order. Cheap to write, easy to tail, easy to ship to
  log aggregators. Idempotent: writing a span whose id is already in the
  file is a no-op.
* **Plain-text refs (`refs/heads/<name>`)** — a branch is one line of
  text. Move a branch by `echo <span_id> > .clew/refs/heads/main`.
  Inspectable, scriptable, diffable.
* **Optional SQLite (`index.sqlite`)** — accelerates `clew log`,
  `clew search`, and TUI filters. Reconstructable from `objects/span/`
  in O(n). Never the source of truth.

### 4.2 Invariants

The store enforces the following invariants. Violating any of them is a
bug:

1. **Append-only.** A span file at `objects/span/<id[:2]>/<id[2:]>` is
   written exactly once. Rewriting the same content is a no-op.
   Rewriting *different* content under the same path is impossible (the
   path *is* the hash).
2. **Hash integrity.** Every read of a span file is verified against
   its filename. A mismatch raises `IntegrityError`.
3. **Ref atomicity.** A ref update is `write-temp + rename`. A crashed
   process leaves either the old or the new value, never partial.
4. **Lock discipline.** A `clew` process that holds `locks/<pid>` is
   the only writer for spans on that trace. Other writers either wait
   or fail fast.
5. **Forward-only branches.** A branch ref can move to any span id the
   caller has; there is no enforcement of "advances only." That is
   the user's call. We log it in `logs/HEAD.log`.

---

## 5. CLI surface

The CLI is a Typer app (entry point `clew.cli:app`). Commands are
designed to feel familiar to anyone who has used git.

| Command | Purpose |
| --- | --- |
| `clew init` | Create a new `.clew/` directory in the current project |
| `clew record` | Run a script under tracing, persist the resulting trace |
| `clew replay` | Re-execute a trace (or a suffix) against a new model/params |
| `clew branch <name> [<base>]` | Create a branch at the current head (or at `<base>`) |
| `clew checkout <ref>` | Move HEAD to a branch or a span id |
| `clew log` | Show the commit-style history of a branch |
| `clew diff <a> <b>` | Structural diff between two spans / traces / branches |
| `clew show <span>` | Render a single span in human-readable form |
| `clew search` | Filter spans by type, attribute, time range, status |
| `clew share` | Export a portable, signed `.clew.bundle` |
| `clew share-open <bundle>` | Verify and import a bundle from a peer |
| `clew tui` | Launch the interactive Textual viewer (browse, branch, replay) |
| `clew status` | Show HEAD, current branch, dirty (uncommitted) spans |
| `clew gc` | Garbage-collect unreachable spans (default: dry-run) |

The CLI is *not* implemented in this task — it is owned by the `cli`
task. The entry point `clew.cli:app` is a stub that other tasks will
populate.

---

## 6. SDK surface

The Python SDK lives in `clew.sdk`. It is the public face of clew for
agent developers. It is intentionally small.

### 6.1 `@tracer.span` decorator

```python
from clew import Tracer

tracer = Tracer.init()  # reads .clew/ from cwd or creates one

@tracer.span(type="LLM", name="plan")
def plan(user_goal: str) -> str:
    return call_openai(user_goal)

@tracer.span(type="TOOL", name="search_web")
def search_web(query: str) -> list[str]:
    return ddg_search(query)
```

Every decorated call records an `LLM` or `TOOL` span automatically,
including start/end timestamps, input, output, model, token counts,
latency, and any exception as an `ERROR` status.

### 6.2 Context manager

```python
with tracer.decision("choose_tool") as d:
    d.set("candidates", ["search_web", "calculator"])
    chosen = pick(candidates)
    d.set("chosen", chosen)
```

The context manager records a `DECISION` span; the `d.set(...)` calls
populate attributes. After exit, the user can branch from this span
and try an alternative candidate.

### 6.3 Task-local span context

Spans nest via a `contextvars.ContextVar` holding the current
`parent_id`. A span started inside another span becomes a child. No
explicit threading is required; `contextvars` is asyncio-safe.

### 6.4 OTel bridge

```python
from clew.sdk.otel import OTelBridge
OTelBridge.install()  # idempotent
```

After `OTelBridge.install()`, every `opentelemetry-api` span created in
the user's process is **mirrored** as a clew span. This means existing
code that uses OTel (`@tracer.start_as_current_span(...)`,
`opentelemetry-instrumentation-openai`, etc.) gets clew branching for
free. The bridge honors the
[OpenTelemetry Generative AI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).

### 6.5 Public API

```python
from clew import (
    Tracer,        # the recorder
    Span,          # the data class (re-exported from clew.core.models)
    Trace,         # the projection
    Branch,        # the named pointer
    __version__,
)
```

---

## 7. Why we win: competitive matrix

Comparison against the four most-cited OSS / freemium agent-observability
projects as of 2026-07. Columns abbreviated: **LS** = LangSmith,
**Ar** = Arize Phoenix, **Lf** = Langfuse, **al** = agentlens.

| Capability | clew | LS | Ar | Lf | al |
| --- | :---: | :---: | :---: | :---: | :---: |
| **Local-first (no server)** | ✅ | ❌ | partial | ❌ | ✅ |
| **Content-addressed spans** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Branch the reasoning DAG** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Replay against new model** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Structural diff of two runs** | ✅ | partial | partial | partial | ❌ |
| **Append-only Merkle DAG** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **OTel semantic conventions** | ✅ | partial | ✅ | partial | ❌ |
| **Portable signed bundle** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Open source (Apache/MIT)** | ✅ | ❌ | ✅ | ✅ | ✅ |
| **Self-hostable** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Works without internet** | ✅ | ❌ | ❌ | ❌ | ✅ |
| **TUI for power users** | ✅ | ❌ | ❌ | ❌ | partial |
| **Replay a suffix only (from span N)** | ✅ | partial | partial | partial | ❌ |

**Reading the table.** Every other project in this space was built to
**observe** agents in production. clew is built to **debug** them. The
branching and content-addressing columns are where clew is alone; the
replay, diff, and suffix-replay rows are where clew is *strictly
stronger*. clew is the only project that says "an agent run is a git
repo, treat it like one."

### 7.1 What we are not

To be clear about scope:

* clew is **not** a metrics / cost dashboard (use Arize / Langfuse).
* clew is **not** an eval framework (use `parea`, `braintrust`).
* clew is **not** a prompt playground (use LangSmith, `promptfoo`).
* clew **is** the missing tool for "the agent did something weird — show
  me why, and let me try the alternative."

---

## 8. Killer features

1. **Branch any reasoning step.** The only OSS debugger that lets you
   say "fork at span 7, take the other path" and re-execute just the
   tail.
2. **Content-addressed, dedup-by-default.** Identical LLM responses
   share one file on disk; identical tool calls across runs merge for
   free in the TUI.
3. **Replay against a different model — without re-running the prefix.**
   Cost-saving superpower: only the divergent suffix is billed.
4. **Signed portable bundles.** Email a `.clew.bundle` to a colleague;
   they get a fully replayable trace, offline, in one command.
5. **OTel-native.** Drop clew into any code that already uses OTel and
   it lights up; clew honors the
   [gen_ai.* semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
6. **Local-first, no SaaS, no account.** Your agent's reasoning is
   sensitive; clew never leaves the box unless you `clew share`.
7. **git-familiar UX.** Anyone who has used `git log`, `git diff`,
   `git checkout`, or `git bundle` already knows clew.

---

## 9. Non-goals (v0.1.0)

* Real-time multi-user collaboration (v0.2+ — federated remotes).
* A hosted cloud product (we will never build one; this is a tool, not a
  SaaS).
* Telemetry-style aggregation / dashboards.
* Eval / scoring / regression detection.
* A non-Python SDK (Python is the lingua franca of agent frameworks;
  others can read the JSONL directly).

---

## 10. Open questions for the team

* **Span retention.** Do we garbage-collect unreachable spans by default,
   or do we keep them forever (git-like, no `gc` until requested)?
   *Proposal: keep forever; `clew gc` is explicit and dry-run by default.*
* **Concurrency model.** File-locks per trace, or per repository?
   *Proposal: per-trace; cross-trace writes are independent.*
* **Bundle signature algorithm.** Ed25519 (small, fast) vs RSA-PSS
   (compatibility). *Proposal: Ed25519; document the verification
   command.*
* **Schema evolution.** When we add a field to `Span`, old bundles must
   still replay. *Proposal: `extra="forbid"` on input, but loaders
   tolerate unknown fields with a warning. v0.1.0 has zero migrations
   to worry about.*

---

*End of architecture document. See `PROTOCOL.md` for byte-level format
specification, and `src/clew/core/models.py` for the canonical data
model.*
