"""Rich-based renderers for the clew CLI.

These helpers turn clew data structures (Trace, TraceDiff, list of
trace summaries) into rich renderables for terminal display. The
color scheme follows git-style conventions: green for added, red
for removed, yellow for modified, dim for unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

if TYPE_CHECKING:
    from clew.core.diff import TraceDiff
    from clew.core.models import Span, Trace


_STATUS_STYLE: dict[str, str] = {
    "OK": "green",
    "ERROR": "red",
    "RUNNING": "yellow",
}


def _span_label(span: Span) -> Text:
    """Format a span's name + status for tree display."""
    style = _STATUS_STYLE.get(str(span.status), "white")
    label = Text()
    label.append(f"{span.name} ", style="bold")
    label.append(f"[{span.type}] ", style="dim")
    label.append(str(span.status), style=style)
    if span.ended_at and span.started_at:
        try:
            dur_ms = (span.ended_at - span.started_at).total_seconds() * 1000
            label.append(f"  ({dur_ms:.1f}ms)", style="dim")
        except Exception:
            pass
    return label


def render_span_tree(trace: Trace) -> RenderableType:
    """Render a trace as a tree of spans (parents before children)."""
    # Build a parent → children map.
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
    tree = Tree(
        f"[bold]{trace.trace_id[:12]}[/bold]…  ({len(trace.spans)} spans)"
    )
    rendered: set[str] = set()

    def render_under(span_id: str, parent: Tree) -> None:
        if span_id in rendered:
            return
        rendered.add(span_id)
        span = by_id[span_id]
        node = parent.add(_span_label(span))
        for child_id in children.get(span_id, []):
            render_under(child_id, node)

    for root_id in roots:
        render_under(root_id, tree)
    # If any spans weren't reached (orphan), attach them at top level.
    for s in trace.spans:
        if s.id not in rendered:
            tree.add(_span_label(s))
    return tree


def render_log(traces: list[dict[str, Any]]) -> Table:
    """Render a list of trace summaries as a rich table.

    ``traces`` is a list of dicts with keys: trace_id, root_name,
    span_count, started_at.
    """
    table = Table(title="clew traces", show_lines=False)
    table.add_column("trace id", style="cyan", no_wrap=True)
    table.add_column("root", style="bold")
    table.add_column("spans", justify="right")
    table.add_column("started", style="dim")
    for row in traces:
        table.add_row(
            row["trace_id"][:12] + "…",
            row.get("root_name", "?"),
            str(row.get("span_count", 0)),
            row.get("started_at", ""),
        )
    return table


def render_diff(diff: TraceDiff) -> RenderableType:
    """Render a TraceDiff as a rich, color-coded panel."""
    lines: list[RenderableType] = []
    lines.append(
        Text.assemble(
            ("--- ", "red"),
            (diff.trace_id_a[:12], "red"),
            ("\n", ""),  # type: ignore[arg-type]
            ("+++ ", "green"),
            (diff.trace_id_b[:12], "green"),
            ("\n", ""),  # type: ignore[arg-type]
            (
                f"@@ {len(diff.modified)} modified, +{len(diff.added)} -{len(diff.removed)}, {diff.unchanged_count} unchanged @@",
                "dim",
            ),
        )
    )
    for a, _b in diff.modified:
        lines.append(
            Text.assemble(("~ ", "yellow"), (a.name, "bold"), (f"  {a.output!r}", "dim"))
        )
    for span in diff.added:
        lines.append(Text.assemble(("+ ", "green"), (span.name, "bold"), (f"  {span.output!r}", "green")))
    for span in diff.removed:
        lines.append(Text.assemble(("- ", "red"), (span.name, "bold"), (f"  {span.output!r}", "red")))
    return Panel(Group(*lines), title="clew diff", border_style="blue")
