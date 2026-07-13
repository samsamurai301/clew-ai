"""Real LLM agent: instrument OpenAI or Anthropic with clew, with a
mock fallback so the example always runs.

The agent is a 3-step research chain: plan → search → answer.
With a real API key in the environment, it calls OpenAI (or
Anthropic, depending on which key is set). Without one, it
falls back to a deterministic mock so the trace looks the same.

Run from the repo root:

    uv run python examples/real_llm_agent.py                  # mock
    OPENAI_API_KEY=sk-... uv run python examples/real_llm_agent.py   # real
    ANTHROPIC_API_KEY=sk-ant-... uv run python examples/real_llm_agent.py  # real
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from clew.sdk import SpanType, Tracer
from clew.sdk.otel import instrument_anthropic, instrument_openai

# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


USE_OPENAI = bool(os.environ.get("OPENAI_API_KEY"))
USE_ANTHROPIC = bool(os.environ.get("ANTHROPIC_API_KEY"))
USE_MOCK = not (USE_OPENAI or USE_ANTHROPIC)

if USE_MOCK:
    print(">>> no API key found; using mock backend (set OPENAI_API_KEY or ANTHROPIC_API_KEY for real LLM calls)")
    BACKEND = "mock"
elif USE_OPENAI:
    print(">>> using OpenAI")
    BACKEND = "openai"
else:
    print(">>> using Anthropic")
    BACKEND = "anthropic"


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


def run_agent(t: Tracer, query: str) -> str:
    """Run a 3-step research agent and return the final answer.

    Span layout:

        run_agent (root, agent)
            plan   (LLM, model=...)
            search (TOOL, mock)
            answer (LLM, model=...)
    """

    @t.agent
    def run(q: str) -> str:
        @t.span("plan", type=SpanType.LLM)
        def plan() -> str:
            return call_llm(
                system="You are a research planner. Output a 1-sentence plan.",
                user=q,
            )

        @t.span("search", type=SpanType.TOOL)
        def search(plan_text: str) -> str:
            return f"results for: {plan_text[:50]}..."

        @t.span("answer", type=SpanType.LLM)
        def answer(plan_text: str, search_text: str) -> str:
            return call_llm(
                system="You are a research assistant. Answer the user's question using the plan and search results.",
                user=f"Question: {q}\n\nPlan: {plan_text}\n\nResults: {search_text}",
            )

        p = plan()
        s = search(p)
        return answer(p, s)

    return run(query)


# ---------------------------------------------------------------------------
# LLM backends
# ---------------------------------------------------------------------------


def call_llm(system: str, user: str) -> str:
    """Dispatch to the selected backend."""
    if BACKEND == "mock":
        return _mock_call(system, user)
    if BACKEND == "openai":
        return _openai_call(system, user)
    if BACKEND == "anthropic":
        return _anthropic_call(system, user)
    raise RuntimeError(f"unknown backend: {BACKEND}")


def _mock_call(system: str, user: str) -> str:
    """Deterministic mock for offline runs."""
    time.sleep(0.05)  # simulate latency
    if "planner" in system.lower():
        return f"plan: 1) understand the question, 2) search, 3) answer — {user[:30]}"
    return f"[mock] {user[:200]}"


def _openai_call(system: str, user: str) -> str:
    """Real OpenAI call (lazy import so the example works without openai)."""
    from openai import OpenAI  # type: ignore[import-not-found]

    client = OpenAI()
    instrument_openai(client)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""


def _anthropic_call(system: str, user: str) -> str:
    """Real Anthropic call (lazy import)."""
    from anthropic import Anthropic  # type: ignore[import-not-found]

    client = Anthropic()
    instrument_anthropic(client)
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text  # type: ignore[index]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    workdir = Path.cwd()
    print(f">>> workdir: {workdir}")
    if not (workdir / ".clew").exists():
        import subprocess

        subprocess.run(["clew", "init", str(workdir)], check=True)

    t = Tracer(cwd=workdir)
    query = sys.argv[1] if len(sys.argv) > 1 else "what is clew?"
    print(f">>> query: {query!r}")
    answer = run_agent(t, query)
    print(f">>> answer: {answer}")

    # Find the trace id and dump the last 3 spans.
    import json
    import subprocess

    log = subprocess.run(
        ["clew", "log", "--json", "--root", str(workdir / ".clew")],
        capture_output=True,
        text=True,
        check=True,
    )
    # clew log --json emits one JSON object per line; pick the last one.
    lines = [ln for ln in log.stdout.splitlines() if ln.strip()]
    last = json.loads(lines[-1])
    tid = last["trace_id"]
    print(f">>> trace_id: {tid}")
    print(f">>> spans: {last['span_count']}")

    # Export the trace to HTML for sharing.
    html_path = workdir / "trace.html"
    subprocess.run(
        ["clew", "show", tid, "--html", str(html_path), "--root", str(workdir / ".clew")],
        check=True,
    )
    print(f">>> html report: {html_path}")


if __name__ == "__main__":
    main()
