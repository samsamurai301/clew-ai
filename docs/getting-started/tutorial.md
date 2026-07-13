# Tutorial: branch a reasoning trace

This tutorial walks through the killer clew workflow: you ran an
agent, the answer wasn't great, you want to try a different model
and see what would have happened.

We'll use a real example: a 3-step research agent answering
"what is clew?". The agent is a mock — the LLM is a deterministic
stub — so the tutorial runs offline and is fully reproducible.

## 1. Install and init

```bash
uv venv && source .venv/bin/activate
uv add clew
mkdir clew-demo && cd clew-demo
clew init .
```

You now have a `.clew/` directory in your project.

## 2. Write a traced agent

Save this as `agent.py`:

```python title="agent.py"
from clew.sdk import Tracer, SpanType

t = Tracer()  # uses ./.clew by default

@t.agent
def research(question: str) -> str:
    @t.span("plan", type=SpanType.LLM)
    def plan():
        # Pretend this calls an LLM.
        return f"plan: answer '{question}' in 3 sentences"

    @t.span("search", type=SpanType.TOOL)
    def search(plan_text: str):
        # Pretend this calls a search API.
        return f"results for: {plan_text}"

    @t.span("answer", type=SpanType.LLM)
    def answer(plan_text: str, search_text: str):
        return f"{plan_text} | {search_text}"

    p = plan()
    s = search(p)
    return answer(p, s)

if __name__ == "__main__":
    import sys
    print(research(sys.argv[1] if len(sys.argv) > 1 else "what is clew?"))
```

Run it:

```bash
python agent.py
# plan: answer 'what is clew?' in 3 sentences | results for: plan: ...
```

## 3. List and inspect

```bash
clew log
# 1 trace, 4 spans (1 root + 3 children)

clew show <trace_id>
# a tree showing plan -> search -> answer
```

Replace `<trace_id>` with the one from `clew log` (or use
`--root .` to scope the lookup).

## 4. Branch: try a different answer

The plan looks fine but the answer was clipped. Let's branch at
the `answer` span and try a different model.

```bash
# Find the answer span's id
clew show <trace_id> --json | grep '"name": "answer"' | head -1
# {"id": "...", "name": "answer", ...}

# Branch from it
clew branch alt <span_id>
# Created branch 'alt' → ...

# Checkout the new branch
clew checkout alt
```

Now edit `agent.py` to take a model parameter and run again:

```python
import sys
print(research(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "gpt-4o"))
```

```bash
python agent.py "what is clew?" gpt-4o-mini
# new trace, same structure, different model output
```

## 5. Diff the two traces

```bash
clew diff <original_trace> <new_trace>
```

The diff shows the `answer` span modified, with the before/after
outputs side-by-side. The `plan` and `search` spans are unchanged
(their inputs and outputs are bit-identical, so they share a span
id — content addressing doing its job).

## 6. Share with a teammate

```bash
# Generate a signing key (do this once)
clew keygen --out ~/.clew/key.pem

# Export the alt branch as a signed bundle
clew share <new_trace> --key ~/.clew/key.pem --out alt.tgz

# Teammate verifies + imports
clew verify alt.tgz --public-key clew-pub.pem
clew import alt.tgz --public-key clew-pub.pem --branch shared
```

## 7. Clean up

```bash
clew doctor
# everything looks healthy

clew gc --dry-run
# scanned 12 spans, would delete 0, kept 12
```

## What next?

- [SDK reference](../reference/sdk.md) — the full Python API
- [CLI reference](../reference/cli.md) — every command
- [Internals](../internals/architecture.md) — how the DAG works
