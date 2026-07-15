# Quickstart

## Run the offline walkthrough

```bash
uvx clew-ai demo
```

This is the fastest complete tour: failure → replay → branch → diff → HTML report.

## Trace your code

```bash
uv add clew-ai
clew init
```

```python
from clew.sdk import SpanType, Tracer

tracer = Tracer()

@tracer.agent
def answer(question: str) -> str:
    return search(question)[0]

@tracer.span("search", type=SpanType.TOOL)
def search(question: str) -> list[str]:
    return [f"local result for {question}"]

answer("What failed?")
```

```bash
python agent.py
clew log
clew show TRACE_ID
clew doctor
```

The store contains a v2 manifest, canonical `.json` span records, atomic refs, and a
rebuildable SQLite WAL index.

## Replay a candidate change

```bash
clew replay TRACE_ID --executor mock
clew replay TRACE_ID --executor my_project.replay:execute --from SPAN_ID --branch fix
clew diff TRACE_ID NEW_TRACE_ID
clew show NEW_TRACE_ID --html report.html
```

A failed executor still leaves a diagnostic trace. The failed occurrence is `ERROR`,
dependent descendants are `SKIPPED`, the trace ID is printed, and the command exits
nonzero.

## Share a signed trace

```bash
clew keygen --out signing-key.pem
clew share TRACE_ID --key signing-key.pem --out trace.tgz
clew verify trace.tgz --public-key signing-key.pem.pub
```

Bundles are plaintext. The signature protects integrity and authenticates the signing key;
encrypt the file separately if its payloads are sensitive.
