"""Interactive TUI for browsing clew traces.

A 3-pane textual interface:
- Left: list of traces
- Right: tree of spans in the selected trace
- Bottom: details of the selected span

Keybindings:
- q — quit
- enter — expand/collapse span
- b — create branch from selected span
- r — replay selected trace
- d — diff with current branch
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Static, Tree
from textual.widgets.tree import TreeNode

from clew.core.branch import BranchManager
from clew.core.diff import diff as diff_traces
from clew.core.models import Span
from clew.core.replay import MockExecutor, ReplayEngine
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.ui.render import _terminal_safe


def _span_tree_label(span: Span) -> Text:
    label = Text()
    label.append(f"[{_terminal_safe(span.type)}] ", style="dim")
    label.append(_terminal_safe(span.name), style="bold")
    label.append(f" ({_terminal_safe(span.status)})")
    return label


def _span_details(span: Span) -> Text:
    details = Text()
    details.append(_terminal_safe(span.name), style="bold")
    details.append(f"  [{_terminal_safe(span.type)}]  {_terminal_safe(span.status)}\n")
    details.append(f"id: {_terminal_safe(span.id)}\n")
    details.append(f"parent_ids: {_terminal_safe(span.parent_ids)}\n")
    details.append("input: " + _terminal_safe(json.dumps(span.input, indent=2, default=str)) + "\n")
    details.append(
        "output: " + _terminal_safe(json.dumps(span.output, indent=2, default=str)) + "\n"
    )
    details.append(
        "attributes: " + _terminal_safe(json.dumps(span.attributes, indent=2, default=str))
    )
    return details


class _TraceList(DataTable[str]):
    """The list of traces on the left side."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("trace", "spans")


class _SpanTree(Tree[dict[str, str] | None]):
    """The span tree for the selected trace."""

    def __init__(self, label: str = "trace", *, id: str | None = None) -> None:
        super().__init__(label, id=id)
        self.show_root = False


class TraceBrowserApp(App[None]):
    """Top-level TUI app."""

    CSS = """
    #left { width: 30%; border-right: solid green; }
    #right { width: 70%; }
    #details { height: 30%; border-top: solid green; padding: 1 2; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("b", "branch", "Branch"),
        Binding("r", "replay", "Replay"),
        Binding("d", "diff", "Diff"),
    ]

    selected_trace: reactive[str | None] = reactive(None)
    selected_span: reactive[str | None] = reactive(None)

    def __init__(self, clew_root: Path) -> None:
        super().__init__()
        self._clew_root = clew_root
        self._store = Store(clew_root)
        self._ts = TraceStore(self._store)
        self._bm = BranchManager(self._ts)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            with Vertical(id="left"):
                yield _TraceList(id="traces")
            with Vertical(id="right"):
                yield _SpanTree("spans", id="span_tree")
        yield Static("select a span for details", id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "clew — local agent what-if debugger"
        self.refresh_traces()

    def refresh_traces(self) -> None:
        """Reload the trace list from the store."""
        table = self.query_one("#traces", _TraceList)
        table.clear()
        for trace_id in self._store.iter_traces():
            try:
                t = self._ts.get_trace(trace_id)
            except KeyError:
                continue
            table.add_row(trace_id[:12] + "…", str(len(t.spans)), key=trace_id)
        if table.row_count:
            table.focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Update the span tree when a trace is selected."""
        if event.control.id != "traces":
            return
        trace_id = event.row_key.value if event.row_key else None
        if not trace_id:
            return
        self.selected_trace = str(trace_id)
        self.refresh_span_tree()

    def refresh_span_tree(self) -> None:
        """Populate the span tree for the selected trace."""
        tree = self.query_one("#span_tree", _SpanTree)
        tree.clear()
        if self.selected_trace is None:
            return
        try:
            trace = self._ts.get_trace(self.selected_trace)
        except KeyError:
            return
        by_id: dict[str, Span] = {s.id: s for s in trace.spans}
        children: dict[str, list[str]] = {s.id: [] for s in trace.spans}
        roots: list[str] = []
        for s in trace.spans:
            if not s.parent_ids or not any(p in by_id for p in s.parent_ids):
                roots.append(s.id)
            else:
                for p in s.parent_ids:
                    if p in by_id:
                        children.setdefault(p, []).append(s.id)
        rendered: set[str] = set()

        stack: list[tuple[str, TreeNode[dict[str, str] | None]]] = [
            (root_id, tree.root) for root_id in reversed(roots)
        ]
        while stack:
            span_id, parent = stack.pop()
            if span_id in rendered:
                continue
            rendered.add(span_id)
            span = by_id[span_id]
            node = parent.add(_span_tree_label(span), data={"span_id": span.id})
            for child_id in reversed(children.get(span_id, [])):
                stack.append((child_id, node))

        tree.show_root = True
        tree.root.set_label(Text(f"{self.selected_trace[:12]}… ({len(trace.spans)} spans)"))

    def on_tree_node_selected(
        self,
        event: Tree.NodeSelected[dict[str, str] | None],
    ) -> None:
        """When a span is selected, show its details."""
        data = event.node.data
        if not isinstance(data, dict):
            return
        span_id = data.get("span_id")
        if not span_id or not self.selected_trace:
            return
        self.selected_span = str(span_id)
        try:
            trace = self._ts.get_trace(self.selected_trace)
        except KeyError:
            return
        span = next((s for s in trace.spans if s.id == span_id), None)
        if span is None:
            return
        details = self.query_one("#details", Static)
        details.update(_span_details(span))

    # -- actions --------------------------------------------------------

    def action_branch(self) -> None:
        """Create a branch at the selected span."""
        if not (self.selected_trace and self.selected_span):
            return
        name = f"branch-{self.selected_span[:6]}"
        self._bm.create(name, self.selected_span)
        self.refresh_traces()

    async def action_replay(self) -> None:
        """Replay the selected trace with the mock executor."""
        trace_id = self.selected_trace
        if not trace_id:
            return
        engine = ReplayEngine(self._ts, executor=MockExecutor())
        await engine.replay(trace_id)
        self.refresh_traces()

    def action_diff(self) -> None:
        """Diff the selected trace with another (using a hardcoded second for the demo)."""
        if not self.selected_trace:
            return
        traces = list(self._store.iter_traces())
        if len(traces) < 2:
            self.notify("need at least 2 traces to diff", severity="warning")
            return
        other = next(t for t in traces if t != self.selected_trace)
        a = self._ts.get_trace(self.selected_trace)
        b = self._ts.get_trace(other)
        d = diff_traces(a, b)
        self.notify(
            f"{len(d.modified)} modified, +{len(d.added)} -{len(d.removed)}",
            title="diff vs " + other[:12],
        )
