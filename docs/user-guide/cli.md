# CLI

The package installs equivalent `clew` and `clew-ai` scripts. Run `clew COMMAND --help`
for complete option details.

## Store and capture

```bash
clew init [PATH]
clew trace -- python agent.py
clew version
clew demo [--root PATH] [--report report.html]
```

`init` creates store v2 and refuses an existing v1 store. `trace --` passes the remaining
argument vector directly without invoking a shell. `demo` is offline and exercises the
complete product workflow.

## Inspect

```bash
clew log [--json]
clew show TRACE [--json]
clew show TRACE --html report.html
clew query --name search --type TOOL --status ERROR --metadata model=gpt-4o
```

JSON output is intended for scripts. HTML reports are self-contained and make no network
request.

## Branch, replay, and diff

```bash
clew branch NAME [SPAN]
clew branches [--json]
clew checkout NAME
clew replay TRACE --executor mock
clew replay TRACE --executor module:function --from SPAN --branch NAME
clew diff TRACE_A TRACE_B [--json]
```

Replay prints the new trace ID. If execution produces `ERROR` or `SKIPPED`, it still
persists and prints the diagnostic trace but exits with status 1.

## Signed bundles

```bash
clew keygen --out private.pem [--public-out public.pem]
clew share TRACE --key private.pem --out trace.tgz
clew verify trace.tgz --public-key public.pem
clew import trace.tgz --public-key public.pem [--branch NAME]
```

Only bundle v2 is accepted. Import is idempotent for exact existing bytes and fails on a
conflicting occurrence ID.

## OTel-shaped NDJSON

```bash
clew export TRACE --out trace.ndjson
clew otel-import trace.ndjson [--branch NAME]
```

This is a JSON projection, not OTLP. Import allocates fresh Clew IDs and validates complete
topology.

## Health and optional interfaces

```bash
clew doctor [--json]
clew gc --dry-run [--grace-seconds 300] [--json]
clew bench --spans 10000 --traces 1 --orphans 0
clew tui
clew mcp
```

`doctor` is non-destructive. `gc` deletes only when `--dry-run` is omitted and keeps
unreferenced spans written during the last five minutes by default, preventing collection
from racing a trace that is still being recorded. TUI and MCP require their named extras;
MCP uses stdio.

## Failure policy

Malformed IDs and refs, v1 formats, hash mismatches, conflicting records, missing parents,
duplicate sequence values, cycles, missing traces, invalid executors, and failed signature
verification produce actionable errors and nonzero exit codes. Commands do not silently
select another trace or overwrite conflicting data.
