"""Behavioral tests for the optional Textual trace browser."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from textual.widgets import DataTable, Static

from clew.core.branch import BranchManager
from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.ui.tui import TraceBrowserApp, _SpanTree, _TraceList


def _seed_trace(root: Path, *, name: str) -> Span:
    store = Store(root)
    trace_store = TraceStore(store)
    now = datetime.now(UTC)
    trace_id = uuid4().hex
    root_span = Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=[],
        sequence=0,
        type=SpanType.OBSERVATION,
        name=name,
        input={"question": "hello"},
        output={"answer": name},
        started_at=now,
        ended_at=now,
        status=SpanStatus.OK,
    )
    child = Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=[root_span.id],
        sequence=1,
        type=SpanType.TOOL,
        name=f"{name}-tool",
        input="x",
        output="y",
        started_at=now,
        ended_at=now,
        status=SpanStatus.OK,
    )
    trace_store.add_span(root_span)
    trace_store.add_span(child)
    BranchManager(trace_store).move("main", root_span.id)
    return root_span


@pytest.mark.asyncio
async def test_tui_browse_branch_replay_and_diff(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    first = _seed_trace(root, name="first")
    second = _seed_trace(root, name="second")
    app = TraceBrowserApp(root)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#traces", _TraceList)
        assert table.row_count == 2

        first_key = next(key for key in table.rows if key.value == first.trace_id)
        app.on_data_table_row_highlighted(DataTable.RowHighlighted(table, 0, first_key))
        tree = app.query_one("#span_tree", _SpanTree)
        assert app.selected_trace == first.trace_id
        assert tree.root.children

        child_node = tree.root.children[0]
        app.on_tree_node_selected(_SpanTree.NodeSelected(child_node))
        assert app.selected_span == first.id
        details = app.query_one("#details", Static)
        assert "first" in str(details.render())

        app.action_branch()
        assert app._bm.get(f"branch-{first.id[:6]}").head_span_id == first.id

        before = set(app._store.iter_traces())
        await app.action_replay()
        after = set(app._store.iter_traces())
        assert len(after - before) == 1

        app.selected_trace = second.trace_id
        app.action_diff()
        await pilot.pause()


@pytest.mark.asyncio
async def test_tui_empty_selection_and_event_guards(tmp_path: Path) -> None:
    root = tmp_path / ".clew"
    only = _seed_trace(root, name="only")
    app = TraceBrowserApp(root)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.selected_trace = None
        app.selected_span = None
        app.refresh_span_tree()
        app.action_branch()
        await app.action_replay()
        app.action_diff()

        app.on_data_table_row_highlighted(
            SimpleNamespace(control=SimpleNamespace(id="other"), row_key=None)  # type: ignore[arg-type]
        )
        table = app.query_one("#traces", _TraceList)
        app.on_data_table_row_highlighted(
            SimpleNamespace(control=table, row_key=None)  # type: ignore[arg-type]
        )

        tree = app.query_one("#span_tree", _SpanTree)
        app.on_tree_node_selected(_SpanTree.NodeSelected(tree.root))
        app.on_tree_node_selected(
            SimpleNamespace(node=SimpleNamespace(data={"span_id": "missing"}))  # type: ignore[arg-type]
        )

        app.selected_trace = only.trace_id
        app.selected_span = None
        app.action_branch()
        app.action_diff()
        await pilot.pause()


def test_tui_widget_defaults() -> None:
    tree = _SpanTree(id="tree")
    assert tree.show_root is False
