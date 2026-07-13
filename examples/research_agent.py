"""End-to-end demo: a research agent with clew branching + replay + diff.

This example shows the *killer* clew workflow:

    1. Run a research agent on "what is clew?" with model gpt-4o.
    2. Branch the trace at the LLM call.
    3. Replay the branch with gpt-4o-mini (faster, cheaper).
    4. `clew diff` the two outcomes.

The agent itself is a 3-step LLM chain: plan -> search -> answer.
The "LLM" is a deterministic mock so the example runs offline and
the output is stable for screenshots.

Run from the repo root:

    uv run python examples/research_agent.py

You'll see clew walking through the full cycle with the actual
CLI, end-to-end. The output is a complete log of how a developer
would debug a model change in production.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from clew.core.models import SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.sdk.tracer import Tracer

# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


_RESPONSES: dict[str, str] = {
    "gpt-4o": (
        "clew is a local-first, content-addressed debugger for AI agent "
        "reasoning traces, similar to git but for agent execution paths."
    ),
    "gpt-4o-mini": (
        "clew is a tool that records how AI agents think and lets you "
        "branch and replay the reasoning."
    ),
}


def mock_llm(model: str, prompt: str) -> str:
    """Pretend to call an LLM. The actual model drives the output."""
    # Simulate latency to make the timings meaningful.
    time.sleep(0.05)
    if model in _RESPONSES:
        return _RESPONSES[model]
    raise KeyError(f"unknown model {model!r}")


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


def run_research_agent(t: Tracer, model: str, query: str) -> tuple[str, str]:
    """Run a 3-step research agent and return ``(answer, root_id)``.

    The trace layout is:
        run_research_agent  (root, agent)
            plan            (LLM)
            search          (TOOL, mock)
            answer          (LLM)

    The ``model`` argument is part of every LLM span's *input* so
    the content-addressed span id is unique per model. That's the
    point: switching models should change the trace.
    """
    from clew.sdk.context import current_span

    @t.agent
    def run(q: str, m: str) -> str:
        @t.span("plan", type=SpanType.LLM)
        def plan() -> str:
            return mock_llm(m, f"plan: {q}")

        @t.span("search", type=SpanType.TOOL)
        def search(plan_text: str) -> str:
            return f"results for: {plan_text[:30]}..."

        @t.span("answer", type=SpanType.LLM)
        def answer(plan_text: str, search_text: str) -> str:
            return mock_llm(m, f"answer: {plan_text} | {search_text}")

        p = plan()
        s = search(p)
        out = answer(p, s)
        # The agent's root span is the current active span right
        # before it returns. We capture it before the agent finalizes
        # so the caller can branch / diff on it.
        root = current_span()
        if root is None:
            raise RuntimeError("no active root span — agent not properly initialized")
        t._last_root = root.id  # type: ignore[attr-defined]
        return out

    answer = run(query, model)
    return answer, t._last_root  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The demo
# ---------------------------------------------------------------------------


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="clew-research-"))
    print("=== clew research-agent demo ===")
    print(f"workdir: {workdir}\n")

    try:
        # 1. Initialize the store.
        run_cli("init", str(workdir))
        clew_path = workdir / ".clew"

        # 2. Run the agent with the bigger model.
        print(">>> running agent with gpt-4o")
        t1 = Tracer(cwd=workdir)
        answer1, root_id_1 = run_research_agent(t1, "gpt-4o", "what is clew?")
        # Move main onto the root span so we can branch.
        ts1 = TraceStore(Store(clew_path))
        from clew.core.branch import BranchManager

        BranchManager(ts1).move("main", root_id_1)
        trace_id_1 = ts1._trace_id_of(root_id_1)  # type: ignore[attr-defined]
        print(f"  trace_id (gpt-4o):   {trace_id_1}")
        print(f"  answer:    {answer1!r}\n")

        # 3. Switch to a new branch and run with a different model.
        # (This is what model A/B testing looks like in clew.)
        print(">>> branching: `mini` and running with gpt-4o-mini")
        BranchManager(ts1).create("mini", root_id_1)
        run_cli("checkout", "mini", "--root", str(clew_path))
        t2 = Tracer(cwd=workdir)
        answer2, root_id_2 = run_research_agent(t2, "gpt-4o-mini", "what is clew?")
        # Move the new branch onto the new root.
        BranchManager(ts1).move("mini", root_id_2)
        trace_id_2 = ts1._trace_id_of(root_id_2)  # type: ignore[attr-defined]
        print(f"  trace_id (mini):     {trace_id_2}")
        print(f"  answer:    {answer2!r}\n")

        # 4. Diff the two traces.
        print(">>> diff (gpt-4o -> gpt-4o-mini):")
        diff_out = run_cli(
            "diff",
            trace_id_1,
            trace_id_2,
            "--root",
            str(clew_path),
        )
        print(diff_out)

        # 5. Replay the first trace (gpt-4o) using a mock executor
        # to demonstrate that replay works without re-running the agent.
        print(">>> replay (gpt-4o trace, mock executor):")
        replay_out = run_cli(
            "replay",
            trace_id_1,
            "--executor",
            "mock",
            "--root",
            str(clew_path),
            "--json",
        )
        replay = json.loads(replay_out)
        print(f"  new trace_id:  {replay['new_trace_id']}\n")

        # 6. Doctor + Query.
        print(">>> clew doctor:")
        run_cli("doctor", "--root", str(clew_path))

        print(">>> query: all LLM spans")
        run_cli("query", "--type", "LLM", "--root", str(clew_path))

    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(workdir, ignore_errors=True)


def run_cli(*args: str) -> str:
    """Run the clew CLI in a subprocess and return its stdout."""
    result = subprocess.run(
        [sys.executable, "-m", "clew", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"  CLI failed: {result.stderr}")
        raise SystemExit(result.returncode)
    return result.stdout


# Hack: expose the last root span id from the most recent run.
def _last_root_span_id(self: Tracer) -> str:
    """Return the id of the most recently created root span."""
    if hasattr(self, "_last_root"):
        return self._last_root
    raise RuntimeError("no root span has been recorded yet")


Tracer._last_root_span_id = _last_root_span_id  # type: ignore[attr-defined]

# And expose _trace_id_of on TraceStore.
def _trace_id_of(self: TraceStore, span_id: str) -> str:
    return self.store.get(span_id).trace_id


TraceStore._trace_id_of = _trace_id_of  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
