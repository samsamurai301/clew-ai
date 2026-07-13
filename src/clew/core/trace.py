"""Trace-aware view over a :class:`clew.core.store.Store`.

A *trace* is a Merkle DAG of spans sharing a single ``trace_id``. The
:class:`TraceStore` provides the navigation operations a debugger needs:
topological ordering, ancestor/descendant queries, and a DFS walk that
yields parents before children.

The store remains the source of truth — :class:`TraceStore` is a thin
projection with no extra on-disk state.
"""

from __future__ import annotations

from collections.abc import Iterator

from clew.core.models import Span, Trace
from clew.core.store import Store


class TraceStore:
    """A trace-aware view over a :class:`Store`."""

    def __init__(self, store: Store) -> None:
        """Wrap ``store`` to provide trace navigation operations."""
        self.store = store

    # -- write ------------------------------------------------------------

    def add_span(self, span: Span) -> str:
        """Append a span to the underlying store. Returns the span id."""
        return self.store.put(span)

    # -- read -------------------------------------------------------------

    def get_trace(self, trace_id: str) -> Trace:
        """Return the trace as a :class:`Trace` with spans topologically sorted.

        Raises :class:`KeyError` if no spans exist for ``trace_id``.
        The root is the span whose ``parent_ids`` is empty (or whose
        parents are not part of this trace).
        """
        spans = list(self.store.iter_spans(trace_id=trace_id))
        if not spans:
            raise KeyError(trace_id)
        ordered = self._topological_sort(spans)
        trace_ids = {s.id for s in ordered}
        root = next(
            (
                s
                for s in ordered
                if not s.parent_ids or not any(p in trace_ids for p in s.parent_ids)
            ),
            ordered[0],
        )
        return Trace(trace_id=trace_id, root_span_id=root.id, spans=ordered)

    def walk(self, root_span_id: str) -> Iterator[Span]:
        """Iterate spans in DFS pre-order from ``root_span_id``.

        Parents are always yielded before their children. Each span is
        yielded exactly once even if it has multiple parents in the
        underlying DAG.
        """
        children_map = self._build_children_map()
        visited: set[str] = set()
        # Stack of pending span ids; we yield on pop so the result is
        # pre-order (root before its descendants).
        stack: list[str] = [root_span_id]
        while stack:
            span_id = stack.pop()
            if span_id in visited:
                continue
            visited.add(span_id)
            yield self.store.get(span_id)
            # Push children in reverse so the original order is preserved
            # by the LIFO discipline of the stack.
            for child_id in reversed(children_map.get(span_id, [])):
                if child_id not in visited:
                    stack.append(child_id)

    def ancestors(self, span_id: str) -> list[Span]:
        """Return the parent chain ending at ``span_id``, root first.

        For spans with multiple parents, the first listed parent is
        followed. If a cycle is detected (corrupt store) the chain is
        truncated rather than looping forever.
        """
        chain: list[Span] = []
        seen: set[str] = set()
        current: Span | None = self.store.get(span_id)
        while current is not None:
            chain.append(current)
            if not current.parent_ids:
                break
            next_id = current.parent_ids[0]
            if next_id in seen:
                break
            seen.add(next_id)
            current = self.store.get(next_id)
        chain.reverse()
        return chain

    def descendants(self, span_id: str) -> list[Span]:
        """Return all descendants of ``span_id`` (any depth), in DFS order."""
        children_map = self._build_children_map()
        result: list[Span] = []
        visited: set[str] = set()
        stack: list[str] = list(children_map.get(span_id, []))
        while stack:
            sid = stack.pop()
            if sid in visited:
                continue
            visited.add(sid)
            result.append(self.store.get(sid))
            for child_id in children_map.get(sid, []):
                if child_id not in visited:
                    stack.append(child_id)
        return result

    def roots(self) -> list[Span]:
        """Return every root span in the store (no parents), in insertion order."""
        return [s for s in self.store.iter_spans() if not s.parent_ids]

    # -- internals --------------------------------------------------------

    def _build_children_map(self) -> dict[str, list[str]]:
        """Map ``parent_id → [child_id, ...]`` for every span in the store."""
        children: dict[str, list[str]] = {}
        for span in self.store.iter_spans():
            for parent_id in span.parent_ids:
                children.setdefault(parent_id, []).append(span.id)
        return children

    def _topological_sort(self, spans: list[Span]) -> list[Span]:
        """Sort ``spans`` so that every parent appears before its children.

        Uses Kahn's algorithm. If a cycle is detected (every parent's id
        appears somewhere but no node has in-degree zero) we fall back
        to the input order — the DAG invariant should make this
        unreachable for well-formed traces, but the fallback keeps us
        total.
        """
        by_id: dict[str, Span] = {s.id: s for s in spans}
        in_degree: dict[str, int] = {s.id: 0 for s in spans}
        children: dict[str, list[str]] = {s.id: [] for s in spans}
        for span in spans:
            for parent_id in span.parent_ids:
                if parent_id in by_id:
                    in_degree[span.id] += 1
                    children[parent_id].append(span.id)
        ready: list[str] = sorted(sid for sid, d in in_degree.items() if d == 0)
        result: list[Span] = []
        while ready:
            sid = ready.pop(0)
            result.append(by_id[sid])
            for child_id in children[sid]:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    ready.append(child_id)
        if len(result) != len(spans):
            # Cycle or disconnected; fall back to insertion order.
            return list(spans)
        return result


__all__ = ["TraceStore"]
