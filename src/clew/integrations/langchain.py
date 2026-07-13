"""LangChain callback handler that writes spans to a clew store.

This module is a *soft* integration: importing it does not pull
in `langchain-core` unless the user has it installed. The
handler is constructed lazily — :class:`ClewCallbackHandler`
imports the LangChain base classes on first use, so a project
that doesn't use LangChain never loads it.

The handler is a drop-in for any LangChain component that
accepts a `callbacks` argument:

    from clew.integrations.langchain import ClewCallbackHandler
    cb = ClewCallbackHandler(tracer=t)
    chain.invoke(input, config={"callbacks": [cb]})
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from clew.core.models import SpanType
from clew.sdk.tracer import Tracer


class ClewCallbackHandler:
    """Bridge between LangChain's callback system and clew spans.

    Implements the subset of
    :class:`langchain_core.callbacks.base.BaseCallbackHandler`
    that emits clew spans. The full LangChain interface has
    ~20 methods; we implement the four core ones (chain start/
    end, llm start/end, tool start/end, on_error) and ignore
    the rest.

    Span mapping:

    - ``on_chain_start`` / ``on_chain_end`` → OBSERVATION span
    - ``on_llm_start`` / ``on_llm_end`` → LLM span
    - ``on_tool_start`` / ``on_tool_end`` → TOOL span
    - ``on_chain_error`` / ``on_llm_error`` / ``on_tool_error`` →
      ERROR span (status set, output = error message)

    The handler maintains a stack of active run ids so nested
    calls form a tree. Parent-child relationships are inferred
    from the LangChain ``parent_run_id`` field.
    """

    def __init__(self, tracer: Tracer) -> None:
        """Construct a callback handler bound to ``tracer``."""
        self._tracer = tracer
        # run_id -> span id (we maintain our own mapping)
        self._spans: dict[str, str] = {}
        # run_id -> parent_run_id
        self._parents: dict[str, str | None] = {}

    # -- helpers ---------------------------------------------------------

    def _classify(self, serialized: dict[str, Any] | None) -> SpanType:
        """Decide the SpanType from a LangChain serialized payload."""
        if not serialized:
            return SpanType.OBSERVATION
        name = str(serialized.get("name", "")).lower()
        if "chat" in name or "llm" in name or "openai" in name or "anthropic" in name:
            return SpanType.LLM
        if "tool" in name:
            return SpanType.TOOL
        return SpanType.OBSERVATION

    def _name(self, serialized: dict[str, Any] | None, default: str) -> str:
        if serialized and "name" in serialized:
            return str(serialized["name"])
        return default

    def _open(self, run_id: UUID, parent_run_id: UUID | None, name: str, type: SpanType) -> None:
        """Open a span and store it keyed by run_id."""
        self._parents[str(run_id)] = str(parent_run_id) if parent_run_id else None
        span = self._tracer._begin(name=name, type=type)
        self._spans[str(run_id)] = span.id

    def _close(self, run_id: UUID, output: Any = None, error: BaseException | None = None) -> None:
        """Close a span and pop it from the active stack."""
        sid = self._spans.pop(str(run_id), None)
        if sid is None:
            return
        self._parents.pop(str(run_id), None)
        self._tracer._end(sid, output=output, error=error)

    # -- chain events ----------------------------------------------------

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any] | None,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._open(
            run_id,
            parent_run_id,
            self._name(serialized, "chain"),
            SpanType.OBSERVATION,
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any] | None,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._close(run_id, output=outputs)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._close(run_id, output=None, error=error)

    # -- LLM events ------------------------------------------------------

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._open(
            run_id,
            parent_run_id,
            self._name(serialized, "llm"),
            SpanType.LLM,
        )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        # response is a LLMResult with .generations
        try:
            text = response.generations[0][0].text
        except (AttributeError, IndexError, TypeError):
            text = str(response)
        self._close(run_id, output=text)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._close(run_id, output=None, error=error)

    # -- tool events -----------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._open(
            run_id,
            parent_run_id,
            self._name(serialized, "tool"),
            SpanType.TOOL,
        )

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._close(run_id, output=output)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._close(run_id, output=None, error=error)


__all__ = ["ClewCallbackHandler"]
