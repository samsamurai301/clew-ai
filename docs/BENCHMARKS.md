# Performance contract

The 1.1.5 release gate measures one 10,000-span linear trace on the Linux CI runner:

- record and persist all spans in under 30 seconds;
- fetch and validate the complete trace in under 5 seconds;
- query that trace in under 5 seconds; and
- show no quadratic increase as the trace grows.

Run the exact gate with:

```bash
uv run pytest -q -s \
  tests/test_scaling.py::test_store_handles_10_000_spans_with_release_limits
```

## Local reference result

On 2026-07-15, the current worktree produced this result on an Apple Silicon macOS
development machine with Python 3.12:

```text
10000 spans: build=17.671s fetch=1.216s query=1.181s
```

This is a development reference, not the release result. The GitHub Actions Linux run is
authoritative because hosted-runner load and filesystem behavior differ. The test records
environment-independent thresholds and prints raw timings so regressions remain visible.

Trace validation and topological ordering use linear graph passes plus heap ordering; they
do not rescan the entire trace for every span. SQLite is a rebuildable query index, while
the canonical JSON record and verified `content_hash` remain the source of truth.
