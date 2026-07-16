"""End-to-end offline research-agent workflow with replay and diff.

This example shows Clew's local what-if workflow:

    1. Run a research agent on "what is clew?" with model gpt-4o.
    2. Create a branch ref for the recorded trace.
    3. Record a second run and replay the original with the mock executor.
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
    "gpt-4o": ("Clew is a zero-server, Git-like what-if debugger for Python agent traces."),
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


def run_research_agent(t: Tracer, model: str, query: str) -> tuple[str, str, str]:
    """Run a 3-step research agent and return answer, trace id, and root id.

    The trace layout is:
        run_research_agent  (root, agent)
            plan            (LLM)
            search          (TOOL, mock)
            answer          (LLM)

    Every recorded occurrence receives a fresh ID. The model remains in
    the root input so the trace also describes which configuration ran.
    """

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
        return answer(p, s)

    before = set(t.store.store.iter_traces())
    answer = run(query, model)
    created = set(t.store.store.iter_traces()) - before
    if len(created) != 1:
        raise RuntimeError(f"expected one new trace, found {len(created)}")
    trace_id = created.pop()
    trace = t.store.get_trace(trace_id)
    return answer, trace_id, trace.root_span_id


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
        answer1, trace_id_1, root_id_1 = run_research_agent(t1, "gpt-4o", "what is clew?")
        # Move main onto the root span so we can branch.
        ts1 = TraceStore(Store(clew_path))
        from clew.core.branch import BranchManager

        BranchManager(ts1).move("main", root_id_1)
        print(f"  trace_id (gpt-4o):   {trace_id_1}")
        print(f"  answer:    {answer1!r}\n")

        # 3. Switch to a new branch and run with a different model.
        # (This is what model A/B testing looks like in clew.)
        print(">>> branching: `mini` and running with gpt-4o-mini")
        BranchManager(ts1).create("mini", root_id_1)
        run_cli("checkout", "mini", "--root", str(clew_path))
        t2 = Tracer(cwd=workdir)
        answer2, trace_id_2, root_id_2 = run_research_agent(t2, "gpt-4o-mini", "what is clew?")
        # Move the new branch onto the new root.
        BranchManager(ts1).move("mini", root_id_2)
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


if __name__ == "__main__":
    main()
