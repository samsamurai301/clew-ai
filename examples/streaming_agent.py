"""Streaming LLM example: instrument a streaming chat completion and
record every token chunk as a child span.

This example uses a mock LLM (no API key required). The streaming
shape is identical to OpenAI / Anthropic streaming; just swap
``_mock_stream`` for the real client.

Run:

    uv run --project . python examples/streaming_agent.py

Then:

    uv run --project . clew log
    uv run --project . clew show <trace_id>
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clew.sdk import SpanType, Tracer

t = Tracer(cwd=Path.cwd())


def _mock_stream(prompt: str) -> Iterator[str]:
    """Yield tokens one at a time. Real LLM streaming looks like this."""
    tokens = [
        "clew ",
        "is ",
        "a ",
        "zero-server ",
        "debugger ",
        "for ",
        "It ",
        "records ",
        "every ",
        "step ",
        "of ",
        "an ",
        "agent ",
        "run, ",
        "replays ",
        "traces, ",
        "branches, ",
        "and ",
        "diffs ",
        "runs.",
    ]
    for token in tokens:
        time.sleep(0.005)  # simulate network latency
        yield token


# Each token gets its own sub-span via the decorator.
@t.span(name="token")
def _emit_token(text: str) -> str:
    """Record a single token chunk as a child span."""
    return text


@t.span(type=SpanType.LLM, name="chat_completion")
def _stream_chat(prompt: str) -> str:
    """Stream a chat completion, recording every chunk as a child span."""
    return "".join(_emit_token(tok) for tok in _mock_stream(prompt))


@t.agent
def ask(prompt: str) -> str:
    """Top-level agent: stream a chat completion for a prompt."""
    return _stream_chat(prompt)


if __name__ == "__main__":
    # This creates a trace with:
    #   ask (root, OBSERVATION)
    #     └── chat_completion (LLM)
    #         ├── token (OBSERVATION, 20 of them)
    #         ├── token (OBSERVATION)
    #         └── ...
    answer = ask("What is clew?")
    print(f">>> streamed answer: {answer!r}")
    print(">>> run: `clew log` and `clew show` to explore the trace")
