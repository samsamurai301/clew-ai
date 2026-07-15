# clew-trace GitHub Action

Run a step under `clew trace` and upload the resulting trace as
a downloadable artifact. The action wraps any shell command,
records its stdout/stderr/exit code as a single span, and
exports the trace as an HTML report.

## Usage

```yaml
- uses: samsamurai301/clew-ai/.github/actions/clew-trace@main
  with:
    run: pytest tests/
    artifact-name: test-trace
    upload: 'true'
```

The secure default runs the command with a minimal environment and does not upload trace
data. Set `upload: 'true'` only when the command, stdout, and stderr are safe to retain;
Clew traces are debugging records, not a DLP or secret-redaction boundary. When enabled,
the action uploads two artifacts:

- `<artifact-name>/.clew` — the full clew store (you can `clew
  show` any trace with this).
- `<artifact-name>/trace.html` — a self-contained HTML report
  viewable in any browser.

## See also

- [The clew docs](https://github.com/samsamurai301/clew-ai)
