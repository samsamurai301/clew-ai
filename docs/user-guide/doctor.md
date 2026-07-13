# Doctor and GC

Two maintenance commands for keeping your store healthy.

## `clew doctor`

Read-only. Walks the store and reports:

- **Manifest sanity** — `manifest.json` is valid JSON and has
  the required keys.
- **HEAD validity** — `HEAD` is a single line naming a branch
  that exists in `refs/`.
- **Ref targets** — every ref points at a span whose file
  exists and is in the SQLite index.
- **Index consistency** — every file on disk has an index row
  and vice versa.

Each issue is reported with a severity (`error` or `warning`)
and a human-readable description.

```bash
clew doctor
# ╭── clew doctor ──╮
# │ ok  -  no issues found                       │
# ╰──── head: main  branches: 2  spans: 42  ────╯

clew doctor --json
# {
#   "healthy": true,
#   "errors": [],
#   "warnings": [],
#   ...
# }
```

Exits 0 if the store is healthy, 1 if any errors were found.
Warnings still pass.

### Common issues

| Code | Severity | Meaning | Fix |
|---|---|---|---|
| `missing-manifest` | error | Store was never initialized. | `clew init` |
| `corrupt-manifest` | error | `manifest.json` is not valid JSON. | Restore from git. |
| `dangling-ref` | error | A ref points at a span file that's gone. | `clew gc` after restoring the file, or `git checkout` the trace. |
| `dangling-head` | error | `HEAD` names a branch that doesn't exist. | `clew branch main <span_id>` |
| `missing-file` | error | Index references a span file that's gone. | Restore from git. |
| `corrupt-index` | error | SQLite index is unreadable. | Delete `index.sqlite` — the next `Store()` call rebuilds it. |
| `orphan-file` | warning | A span file has no index row. | `clew gc` (it's unreachable anyway). |
| `empty-ref` | warning | A ref file is empty. | `clew branch main <span_id>` |

## `clew gc`

Destructive. Removes span files that are no longer reachable
from any branch.

```bash
# Preview
clew gc --dry-run
# scanned 50 spans, would delete 3, kept 47

# Actually delete
clew gc
# scanned 50 spans, deleted 3, kept 47
```

A span is "orphan" iff it is not in the closure of any ref's
head (i.e., it is not an ancestor of any current branch head).
This is what happens naturally when you delete a branch:
the spans that were only reachable through that branch are
left behind, and `clew gc` cleans them up.

**Use `clew gc --dry-run` first.** It is non-destructive and
prints exactly what would be deleted. Run it once to make sure
the report looks right, then run it for real.

`gc` does not touch the SQLite index, the refs, the manifest,
or HEAD. It only deletes span shard files that no ref can
reach.

## When to use

- **After a long debugging session** — you branch, replay,
  branch, replay, and now you have a dozen branches. `clew
  branch -d` the ones you don't want, then `clew gc` to
  clean up the spans.
- **Before committing** — `clew doctor` should be clean.
  If it's not, your repo state is out of sync with the
  store.
- **Periodically in CI** — a GitHub Action that runs
  `clew doctor` after each trace-capture run catches
  corruption early.

## See also

- [Internals: store layout](../internals/architecture.md#store-layout)
