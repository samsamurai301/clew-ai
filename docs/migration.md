# Compatibility notice: v1 to v2

Clew 1.1.5 is a breaking corrective release. Store format v2 and bundle format v2 are
the only persisted formats it supports.

## Why there is no automatic migration

The v1 identity contract could collapse separate executions onto the same span ID. That
means a generic converter cannot always reconstruct which occurrence, output, ordering,
or parent relationship was intended. Silently guessing would turn ambiguous data into
apparently trustworthy data.

Clew therefore detects v1 stores and raises `UnsupportedStoreVersion` before creating a
lock, index, ref, or replacement record. It never deletes, edits, or upgrades the old
directory automatically.

## Start a v2 store safely

From the project directory:

```bash
mv .clew .clew-v1-archive
clew init
clew doctor
```

Keep the archive until you no longer need the original evidence. Do not copy v1 span
files into the new store.

## Contract changes

| Area | v1 behavior | v2 behavior |
| --- | --- | --- |
| Span identity | Derived/collision-prone | Independent UUID4 occurrence ID |
| Trace identity | Could be coupled to content | Independent UUID4 per execution |
| Integrity | Partial content-style digest | SHA-256 over every persisted field except the hash |
| Record file | One-line `.jsonl` | Canonical `.json` |
| Ordering | Timestamp/insertion assumptions | Unique `sequence` per trace |
| Persisted status | Could expose `RUNNING` | Final `OK`, `ERROR`, or `SKIPPED` only |
| Replay return | Executor could construct spans | Executor returns constrained `ReplayResult` |
| Partial replay | Incomplete parent rewriting | Ancestors cloned and all parents rewritten |
| Bundle | v1 | v2 only |

## Version 1.1.4

Version 1.1.4 is treated as an unsafe launch artifact because of trace-collision behavior
and incorrect public project links. The existing `v1.1.4` Git tag remains historical and
must not be moved or recreated. Its tagged source is documented as not reproducing the
manually uploaded package artifact. The PyPI release should be yanked, not deleted.

## Python API updates

Replay callables now accept a finalized source `Span` and `ReplayContext`, then return a
`ReplayResult` synchronously or asynchronously:

```python
from clew.sdk import ReplayContext, ReplayResult, Span

def execute(span: Span, context: ReplayContext) -> ReplayResult:
    return ReplayResult(output={"name": span.name, "parents": len(context.parent_chain)})
```

The public `Span` is immutable and finalized. In-flight mutable state is internal to the
tracer and is never written to disk.
