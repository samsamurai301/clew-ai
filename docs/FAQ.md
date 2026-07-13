# Frequently asked questions

## What does "clew" mean?

It's from Greek mythology: Ariadne's clew (ball of thread) helped
Theseus find his way out of the labyrinth. The clew library helps
*you* find your way through a maze of LLM reasoning by recording
every step, branching at interesting points, and replaying the
alternatives to see what would have happened.

## How is this different from LangSmith / Arize / Langfuse / agentlens?

Those are observability platforms. They record spans, ship them to a
server, and give you dashboards. `clew` is local-first: your traces
live in a `.clew/` directory in your repo, content-addressed, and
git-style branchable. Nothing leaves your machine unless you
explicitly `clew share` it.

The killer feature is **branching**. When your agent does something
surprising, you can fork the trace at the decision point, replay the
fork with a different prompt or model, and `clew diff` the two
outcomes. None of the observability platforms can do that.

## Why is the store a `.clew` directory instead of a database file?

Because the on-disk layout *is* the API:

- `spans/<aa>/<id>.jsonl` — content-addressed, two identical spans
  share one file. No fragmentation, no dedup, no migrations.
- `refs/<name>` — text files containing one span id. Branching is
  `echo <id> > refs/feature-x`.
- `HEAD` — one line, the current branch. `cat HEAD` tells you
  where you are.
- `manifest.json` + `index.sqlite` — metadata and an index, both
  rebuildable from the span files.

`git` proved this design pattern for source code; we use the same
pattern for reasoning. Everything is plain text (or a rebuildable
SQLite), so you can `grep`, `cat`, `find`, `wc`, and version-control
your way through it.

## Is this a replacement for OpenTelemetry?

No. `clew` is local-first and single-machine; OTel is the standard
for shipping telemetry to a backend. They complement each other:

- `clew`'s OTel bridge (`clew.sdk.otel.instrument_openai`) emits
  spans that are also valid OTel `gen_ai.*` dicts.
- `clew export` writes a trace to NDJSON in OTel's shape, so a
  collector can ingest it.
- `clew otel-import` reads OTel NDJSON back into a `clew` store,
  so you can branch and replay traces you captured elsewhere.

Use `clew` when you need branching. Use OTel when you need a
backend. Use both: instrument once, pipe to either sink.

## How big can a store get?

We haven't measured the upper bound; the design is file-per-span
with a SQLite index, so a million-span store would be ~1M small
files. The filesystem inodes are the limit, not clew.

If you're worried about scale, run `clew doctor` periodically —
it will tell you if the store is in good shape — and `clew gc` to
clean up orphan spans.

## What if I want to ship spans to a backend?

`clew` doesn't ship spans anywhere by default. The closest
thing to a "backend" is `clew share`, which creates a signed
tarball. From there you can:

- Upload to S3 / GitHub Releases and have teammates `clew import`.
- Untar and `clew otel-import` into another store.
- Pipe the `clew export` NDJSON into an OTel collector that
  supports the file exporter.

If you need a real OTel exporter, wrap your tracer with the OTel
SDK and have *it* emit to your backend; clew can coexist.

## How does replay work?

`clew replay <trace>` walks the trace, copies the ancestor chain
into a new trace, and re-executes the descendants with the same
inputs but the current code (or a different executor you provide
via `--executor`). The new trace shares span ids with the old one
where the content is the same (content-addressed dedup), so you
can `clew diff` the old and new traces and the diff highlights
only the changed parts.

If your agent is non-deterministic (real LLMs), the replay will
produce different outputs — that's the point. Replay with a
`MockExecutor` and you get a clean A/B test.

## Why are span ids content-addressed?

Two reasons:

1. **Dedup** — if your agent retries the same call, the second
   attempt's span collapses into the first. You don't get
   duplicate rows in your index.
2. **Branching** — when you replay a trace, ancestors that are
   bit-identical to the original share the same span id. The
   diff engine matches spans by id *or* by path-from-root, so
   modified descendants land side-by-side in the diff view.

The cost is that span ids aren't sequential. We make up for it
with a per-trace topological order so `clew show` renders the
tree in execution order.

## Can I use clew with my existing OTel-instrumented code?

Yes. `clew` doesn't replace OTel; it complements it. Three ways
to combine:

1. **Dual-write**: instrument with both `clew.Tracer` and the
   OTel SDK. The two coexist.
2. **Bridge**: `clew.sdk.otel.instrument_openai` wraps OpenAI
   clients to also emit `clew` spans. Use it on the same clients
   you wrap for OTel.
3. **Round-trip**: capture with OTel, export to NDJSON, import
   into `clew` with `clew otel-import` for branching/replay.

## How do I migrate from v0.x to v1.0?

The on-disk store format has not changed. If you have a `v0.1.0`
store, you can open it with `v1.0.0` and it just works. Two
breaking changes in v1.0.0 affect the *CLI* and the *Python API*:

- `clew share` now requires `--key <private-key>` (the unsigned
  bundle was a v0.1.0 placeholder). Run `clew keygen` once and
  reuse the key.
- `clew bundle import` was renamed to `clew import`. The new
  `clew otel-import` is for the NDJSON form.

See `CHANGELOG.md` for the full list.
