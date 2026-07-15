# OTel-shaped NDJSON

Clew provides a JSON projection that uses familiar OpenTelemetry-style trace/span fields
and `gen_ai.*` attributes. It does **not** implement OTLP transport and 1.1.5 does not claim
interoperability with an OpenTelemetry collector.

Install the optional OTel libraries only if the application needs them:

```bash
uv add 'clew-ai[otel]'
```

## Export and import

```bash
clew export TRACE --format ndjson --out trace.ndjson
clew import trace.ndjson
```

An import creates fresh Clew span IDs and a fresh trace ID, retains source identifiers as
provenance metadata, rewrites complete parent topology, and validates missing parents,
duplicate ordering, roots, and cycles.

```python
from clew.sdk.otel import from_otel_span, to_otel_span

document = to_otel_span(span)
new_occurrence = from_otel_span(document)
```

The single-span helper cannot reconstruct a surrounding topology; use NDJSON bulk import
when parent relationships matter.

## Limits

Input byte and span-count limits protect the importer from unbounded documents. Invalid
JSON, non-object lines, unknown record kinds, malformed fields, and invalid topology fail
with an explicit error rather than silently falling back to another trace.
