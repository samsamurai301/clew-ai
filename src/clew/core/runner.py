"""Record a subprocess as a single trace.

``clew trace -- python my_agent.py`` wraps the command in a single
``OBSERVATION`` span. The span captures:

- The full ``argv`` as the span's ``input``.
- ``stdout`` (decoded as UTF-8, lossily) and ``stderr`` (same) as
  attributes; the *last* line of stdout is the span's ``output``,
  which is what `clew show` displays first.
- Exit code, status (``OK`` if zero, ``ERROR`` otherwise), and the
  full error message in ``error`` if the exit code was non-zero.
- Wall-clock ``started_at`` / ``ended_at``.

This is a thin wrapper — it's here so non-Python agents (a shell
script, a binary) can leave a clew trace without instrumenting their
own code. Use it for one-off runs; for high-fidelity tracing of an
agent, prefer the Python SDK.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.utils.hash import span_hash


def run_and_record(
    argv: list[str],
    *,
    cwd: Path,
    store: Store,
    name: str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float | None = None,
) -> Span:
    """Run ``argv`` and write a single span summarizing the result.

    The span is content-addressed: two runs with the same argv and
    cwd share an id (modulo timestamps, which are excluded from the
    hash by :func:`clew.utils.hash.span_hash`).
    """
    if not argv:
        raise ValueError("argv must contain at least one element")
    span_name = name or Path(argv[0]).name or "subprocess"
    # Build a deterministic hash from the stable parts.
    sentinel = datetime(1970, 1, 1, tzinfo=UTC)
    partial = Span(
        id="",
        trace_id=uuid.uuid4().hex,  # fresh per run
        parent_ids=[],
        type=SpanType.OBSERVATION,
        name=span_name,
        attributes={},
        input={"argv": list(argv), "cwd": str(cwd)},
        output=None,
        started_at=sentinel,
        ended_at=sentinel,
        status=SpanStatus.OK,
    )
    sid = span_hash(partial)
    now = datetime.now(UTC)
    span = partial.model_copy(update={"id": sid, "started_at": now})
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        ended = datetime.now(UTC)
        err_span = span.model_copy(
            update={
                "ended_at": ended,
                "status": SpanStatus.ERROR,
                "error": f"timeout after {timeout_s}s",
                "attributes": {
                    "argv": list(argv),
                    "cwd": str(cwd),
                    "duration_s": time.monotonic() - started,
                    "stdout_tail": _tail(str(exc.stdout or "")),
                    "stderr_tail": _tail(str(exc.stderr or "")),
                },
            }
        )
        store.put(err_span)
        return err_span
    ended = datetime.now(UTC)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    status = SpanStatus.OK if proc.returncode == 0 else SpanStatus.ERROR
    last_line = _last_nonempty_line(stdout)
    err_msg: str | None = None
    if proc.returncode != 0:
        err_msg = (
            f"exit {proc.returncode}: "
            f"{_last_nonempty_line(stderr) or '(no stderr)'}"
        )
    final = span.model_copy(
        update={
            "ended_at": ended,
            "status": status,
            "error": err_msg,
            "output": last_line,
            "attributes": {
                "argv": list(argv),
                "cwd": str(cwd),
                "duration_s": time.monotonic() - started,
                "returncode": proc.returncode,
                "stdout_tail": _tail(stdout),
                "stderr_tail": _tail(stderr),
            },
        }
    )
    store.put(final)
    return final


def _tail(text: str, max_bytes: int = 4096) -> str:
    """Return the last ``max_bytes`` of ``text`` (UTF-8 lossy)."""
    if len(text) <= max_bytes:
        return text
    return "...\n" + text[-max_bytes:]


def _last_nonempty_line(text: str) -> str | None:
    """Return the last line of ``text`` that contains non-whitespace."""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line
    return None


__all__ = ["run_and_record"]
