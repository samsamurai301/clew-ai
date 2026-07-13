"""Branching demo: run an agent twice with different params, diff the traces.

This is the killer-feature demo: a single recorded trace, branched
at a mid-trace span, replayed under a new model, then diffed.

Run with:

    uv run python examples/branching_demo.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clew.core.branch import BranchManager
from clew.core.diff import diff, format_text
from clew.core.replay import ReplayEngine
from clew.sdk import SpanType, Tracer

t = Tracer(cwd=Path.cwd())
bm = BranchManager(t.store)


@t.agent
def run_agent(question: str, model: str = "gpt-4o") -> str:
    @t.span("plan", type=SpanType.DECISION)
    def plan() -> dict[str, str]:
        return {"query": question}

    @t.span("answer", type=SpanType.LLM)
    def answer(plan: dict[str, str]) -> str:
        return f"[{model}] {plan['query']}"

    return answer(plan())


async def _replay_with_model(trace_id: str, branch_name: str, new_model: str) -> str:
    """Replay the trace under a new model, returning the new trace id."""
    from clew.core.replay import RecordingExecutor

    def make_executor(model: str) -> RecordingExecutor:
        async def fn(span, ctx):  # type: ignore[no-untyped-def]
            args = (span.input or {}).get('args') or ['']
            output = f"[{model}] {args[0] if args else ''}"
            return output, {"replay.model": model}
        return RecordingExecutor(fn)

    engine = ReplayEngine(t.store, executor=make_executor(new_model))
    new_trace = await engine.replay(trace_id)
    if branch_name in {b.name for b in bm.list()}:
        bm.move(branch_name, new_trace.root_span_id)
    else:
        bm.create(branch_name, new_trace.root_span_id)
    return new_trace.trace_id


async def main() -> None:
    # Original run.
    original_answer = run_agent("What is clew?", model="gpt-4o")
    print(f"Original: {original_answer}")
    original_trace_id = list(t.store.store.iter_traces())[-1]
    original_trace = t.store.get_trace(original_trace_id)
    if "main" in {b.name for b in bm.list()}:
        bm.move("main", original_trace.root_span_id)
    else:
        bm.create("main", original_trace.root_span_id)
    # Replay under a different model.
    new_trace_id = await _replay_with_model(original_trace_id, "gpt-4o-mini-branch", "gpt-4o-mini")
    print(f"Replayed: {new_trace_id[:12]}...")
    new_trace = t.store.get_trace(new_trace_id)
    # Diff the two.
    d = diff(original_trace, new_trace)
    print()
    print(format_text(d))


if __name__ == "__main__":
    asyncio.run(main())
