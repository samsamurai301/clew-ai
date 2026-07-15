"""Rich-based renderers for the clew CLI.

These helpers turn clew data structures (Trace, TraceDiff, list of
trace summaries) into rich renderables for terminal display. The
color scheme follows git-style conventions: green for added, red
for removed, yellow for modified, dim for unchanged.
"""

from __future__ import annotations

import unicodedata
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
    "SKIPPED": "yellow",
}


def _terminal_safe(value: object) -> str:
    """Render untrusted text without emitting terminal control operations."""
    output: list[str] = []
    for char in str(value):
        if unicodedata.category(char) in {"Cc", "Cf"}:
            codepoint = ord(char)
            if char == "\n":
                output.append("\\n")
            elif char == "\r":
                output.append("\\r")
            elif char == "\t":
                output.append("\\t")
            elif codepoint <= 0xFF:
                output.append(f"\\x{codepoint:02x}")
            else:
                output.append(f"\\u{codepoint:04x}")
        else:
            output.append(char)
    return "".join(output)


def _span_label(span: Span) -> Text:
    """Format a span's name + status for tree display."""
    style = _STATUS_STYLE.get(str(span.status), "white")
    label = Text()
    label.append(f"{_terminal_safe(span.name)} ", style="bold")
    label.append(f"[{_terminal_safe(span.type)}] ", style="dim")
    label.append(_terminal_safe(span.status), style=style)
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
    root_label = Text()
    root_label.append(trace.trace_id[:12], style="bold")
    root_label.append(f"…  ({len(trace.spans)} spans)")
    tree = Tree(root_label)
    rendered: set[str] = set()

    stack: list[tuple[str, Tree]] = [(root_id, tree) for root_id in reversed(roots)]
    while stack:
        span_id, parent = stack.pop()
        if span_id in rendered:
            continue
        rendered.add(span_id)
        node = parent.add(_span_label(by_id[span_id]))
        for child_id in reversed(children.get(span_id, [])):
            stack.append((child_id, node))
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
            Text(_terminal_safe(str(row["trace_id"])[:12] + "…")),
            Text(_terminal_safe(row.get("root_name", "?"))),
            Text(_terminal_safe(row.get("span_count", 0))),
            Text(_terminal_safe(row.get("started_at", ""))),
        )
    return table


def render_diff(diff: TraceDiff) -> RenderableType:
    """Render a TraceDiff as a rich, color-coded panel."""
    lines: list[RenderableType] = []
    lines.append(
        Text.assemble(
            ("--- ", "red"),
            (diff.trace_id_a[:12], "red"),
            ("\n", ""),
            ("+++ ", "green"),
            (diff.trace_id_b[:12], "green"),
            ("\n", ""),
            (
                f"@@ {len(diff.modified)} modified, +{len(diff.added)} -{len(diff.removed)}, {diff.unchanged_count} unchanged @@",
                "dim",
            ),
        )
    )
    for a, _b in diff.modified:
        lines.append(
            Text.assemble(
                ("~ ", "yellow"),
                (_terminal_safe(a.name), "bold"),
                (_terminal_safe(f"  {a.output!r}"), "dim"),
            )
        )
    for span in diff.added:
        lines.append(
            Text.assemble(
                ("+ ", "green"),
                (_terminal_safe(span.name), "bold"),
                (_terminal_safe(f"  {span.output!r}"), "green"),
            )
        )
    for span in diff.removed:
        lines.append(
            Text.assemble(
                ("- ", "red"),
                (_terminal_safe(span.name), "bold"),
                (_terminal_safe(f"  {span.output!r}"), "red"),
            )
        )
    return Panel(Group(*lines), title="clew diff", border_style="blue")
