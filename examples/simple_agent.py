"""A trivial clew agent — three spans: plan, call_tool, respond.

Run with:

    uv run python examples/simple_agent.py

The trace lands in ``./.clew``. Inspect it with:

    uv run --project . clew log
    uv run --project . clew show <trace_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `clew` importable when running from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clew.sdk import SpanType, Tracer

t = Tracer(cwd=Path.cwd())


@t.agent
def run_agent(question: str) -> str:
    """A 3-step agent: plan, call a fake search tool, respond."""
    plan = make_plan(question)
    evidence = fake_search(plan["query"])
    return compose_answer(question, evidence)


@t.span("make_plan", type=SpanType.DECISION)
def make_plan(question: str) -> dict[str, str]:
    """Decide what to search for."""
    return {"query": question.split()[0] if question else "hello"}


@t.span("fake_search", type=SpanType.TOOL)
def fake_search(query: str) -> list[str]:
    """Pretend to search the web."""
    return [
        f"Result for {query}: Clew is a zero-server, Git-like what-if debugger "
        "for Python agent traces."
    ]


@t.span("compose_answer", type=SpanType.LLM)
def compose_answer(question: str, evidence: list[str]) -> str:
    """Stitch the final answer from the evidence."""
    return f"Q: {question}\nA: {' '.join(evidence)}"


if __name__ == "__main__":
    question = "What is clew?"
    answer = run_agent(question)
    print(answer)
    # Quick summary so the user sees the trace was saved.
    store = t.store.store
    trace_ids = list(store.iter_traces())
    print(f"\nTrace saved to: {store.root}")
    print(f"Trace count: {len(trace_ids)}")
    for tid in trace_ids[-1:]:
        trace = t.store.get_trace(tid)
        print(f"  trace {tid[:12]}... spans={len(trace.spans)} root={trace.root_span_id[:12]}...")
