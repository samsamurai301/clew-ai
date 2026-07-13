"""Tests for the clew MCP server (clew.mcp_server)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.mcp_server import build_server


def _make_span(
    trace_id: str,
    *,
    parent_ids: list[str] | None = None,
    name: str = "root",
    type: SpanType = SpanType.OBSERVATION,
    status: SpanStatus = SpanStatus.OK,
    error: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Span:
    return Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=parent_ids or [],
        type=type,
        name=name,
        attributes={},
        input="x",
        output="y",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ended_at=datetime(2024, 1, 2, tzinfo=UTC),
        status=status,
        error=error,
        metadata=metadata,
    )


def _seed(root: Path) -> str:
    """Seed a small trace and return its trace_id."""
    store = Store(root)
    ts = TraceStore(store)
    tid = uuid4().hex
    spans = [
        _make_span(tid, name="agent"),
        _make_span(tid, parent_ids=[], name="llm-call", type=SpanType.LLM),
    ]
    spans[1] = spans[1].model_copy(update={"parent_ids": [spans[0].id]})
    for s in spans:
        ts.add_span(s)
    from clew.core.branch import BranchManager

    BranchManager(ts).move("main", spans[0].id)
    return tid


def _tools(server) -> list:
    """Return the list of registered tools."""
    import asyncio

    from mcp.types import ListToolsRequest

    handler = server.request_handlers[ListToolsRequest]
    result = asyncio.run(handler(None))  # type: ignore[arg-type]
    return result.root.tools


def _resources(server) -> list:
    import asyncio

    from mcp.types import ListResourcesRequest

    handler = server.request_handlers[ListResourcesRequest]
    result = asyncio.run(handler(None))  # type: ignore[arg-type]
    return result.root.resources


def _call(server, name: str, arguments: dict) -> list:
    """Invoke a tool and return the raw content list."""
    import asyncio

    from mcp.types import CallToolRequest, CallToolRequestParams

    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        params=CallToolRequestParams(name=name, arguments=arguments)
    )
    result = asyncio.run(handler(req))
    return result.root.content


def _text(content: list) -> str:
    return content[0].text


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_server_exposes_core_tools() -> None:
    server = build_server()
    tools = _tools(server)
    names = {t.name for t in tools}
    expected = {
        "list_traces",
        "get_trace",
        "get_span",
        "search",
        "list_branches",
        "show_branch",
        "diff_traces",
        "create_branch",
        "checkout",
        "replay",
        "doctor",
        "query",
    }
    assert expected <= names


def test_server_exposes_resources() -> None:
    server = build_server()
    resources = _resources(server)
    assert any(str(r.uri) == "store://info" for r in resources)


# ---------------------------------------------------------------------------
# Tool invocations
# ---------------------------------------------------------------------------


def test_list_traces_returns_seeded(tmp_path: Path) -> None:
    _seed(tmp_path / ".clew")
    out = _call(build_server(), "list_traces", {"root": str(tmp_path / ".clew")})
    parsed = json.loads(_text(out))
    assert len(parsed) == 1
    assert parsed[0]["root_name"] == "agent"


def test_get_trace_returns_spans(tmp_path: Path) -> None:
    tid = _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "get_trace",
        {"trace_id": tid, "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert parsed["trace_id"] == tid
    assert len(parsed["spans"]) == 2


def test_get_span_by_id(tmp_path: Path) -> None:
    store = Store(tmp_path / ".clew")
    s = _make_span(uuid4().hex, name="solo")
    store.put(s)
    out = _call(
        build_server(),
        "get_span",
        {"span_id": s.id, "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert parsed["id"] == s.id
    assert parsed["name"] == "solo"


def test_search_filters(tmp_path: Path) -> None:
    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "search",
        {"type": "LLM", "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert parsed["count"] == 1
    assert parsed["matches"][0]["type"] == "LLM"


def test_list_branches(tmp_path: Path) -> None:
    _seed(tmp_path / ".clew")
    out = _call(build_server(), "list_branches", {"root": str(tmp_path / ".clew")})
    parsed = json.loads(_text(out))
    assert any(b["name"] == "main" for b in parsed)


def test_show_branch(tmp_path: Path) -> None:
    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "show_branch",
        {"name": "main", "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert parsed["branch"] == "main"
    assert len(parsed["spans"]) >= 1


def test_create_branch_and_checkout(tmp_path: Path) -> None:
    tid = _seed(tmp_path / ".clew")
    store = Store(tmp_path / ".clew")
    root_span_id = next(
        s.id for s in store.iter_spans(tid) if not s.parent_ids
    )
    out = _call(
        build_server(),
        "create_branch",
        {
            "name": "experiment",
            "from_span": root_span_id,
            "root": str(tmp_path / ".clew"),
        },
    )
    assert "experiment" in _text(out)
    out = _call(
        build_server(),
        "checkout",
        {"name": "experiment", "root": str(tmp_path / ".clew")},
    )
    assert "experiment" in _text(out)


def test_doctor_healthy_store(tmp_path: Path) -> None:
    _seed(tmp_path / ".clew")
    out = _call(build_server(), "doctor", {"root": str(tmp_path / ".clew")})
    parsed = json.loads(_text(out))
    assert parsed["healthy"] is True


def test_query_tool(tmp_path: Path) -> None:
    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "query",
        {"name": "llm", "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert parsed["count"] == 1


def test_unknown_tool_returns_error(tmp_path: Path) -> None:
    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "definitely_not_a_tool",
        {"root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert "error" in parsed
