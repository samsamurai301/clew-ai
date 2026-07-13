# Exporting

Three ways to get a trace out of clew:

## 1. Signed bundle (`clew share`)

For sharing with a teammate or uploading to a backend. See
[Sharing](sharing.md) for details.

```bash
clew share <trace_id> --key ~/.clew/key.pem --out trace.tgz
```

## 2. OTel NDJSON (`clew export`)

For piping into an OTel collector or any NDJSON consumer.
Compatible with OTel's JSON file exporter.

```bash
clew export <trace_id> --out trace.ndjson
# 1 header + N span lines
```

The output is one JSON object per line:

```json
{"_kind": "trace", "trace_id": "...", "span_count": 4}
{"_kind": "span", "name": "plan", "trace_id": "...", "span_id": "...", ...}
{"_kind": "span", "name": "answer", ...}
```

The leading `_kind: trace` header is a clew extension. Plain
OTel consumers can ignore it; `clew otel-import` uses it to
recover the trace id.

## 3. HTML report (`clew show --html`)

For sharing with a non-developer (or anyone who doesn't want
to install clew). Self-contained, interactive, dark theme,
works offline.

```bash
clew show <trace_id> --html report.html
# report.html
```

Open `report.html` in any browser. The page shows:

- A tree of spans, click to expand each.
- ERROR spans highlighted in red.
- Input / output / attributes / metadata for each span.

Email it, gist it, drop it in S3. No external dependencies, no
CDN, no analytics.

## Comparison

| Format | Use case | Includes spans | Re-importable | Self-contained |
|---|---|---|---|---|
| Signed bundle | Share with a teammate, archive | yes | yes (with public key) | yes (no ext deps) |
| OTel NDJSON | OTel collector, line-by-line tools | yes | yes (via `otel-import`) | yes |
| HTML report | Show to anyone | yes (rendered) | no | yes (single .html) |

## See also

- [Sharing](sharing.md)
- [OTel integration](../integrations/otel.md)
- [CLI reference: show](../reference/cli.md#clew-show)
