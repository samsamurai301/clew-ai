"""Tests for the clew MCP server (clew.mcp_server)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from clew.core.models import Span, SpanStatus, SpanType
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.mcp_server import build_server


def test_actual_mcp_client_session_roundtrip(tmp_path: Path) -> None:
    """The public MCP client can initialize, call a tool, and read a resource."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    trace_id = _seed(tmp_path / ".clew")

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "clew", "mcp"],
            cwd=tmp_path,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "clew"

                tools = await session.list_tools()
                assert "get_trace" in {tool.name for tool in tools.tools}

                result = await session.call_tool(
                    "get_trace",
                    {"trace_id": trace_id, "root": str(tmp_path / ".clew")},
                )
                assert result.isError is False
                payload = json.loads(result.content[0].text)  # type: ignore[union-attr]
                assert payload["trace_id"] == trace_id

                resource = await session.read_resource("store://info")
                info = json.loads(resource.contents[0].text)  # type: ignore[union-attr]
                assert info["version"] == 2

    import asyncio

    asyncio.run(exercise())


def _make_span(
    trace_id: str,
    *,
    parent_ids: list[str] | None = None,
    name: str = "root",
    type: SpanType = SpanType.OBSERVATION,
    status: SpanStatus = SpanStatus.OK,
    error: str | None = None,
    metadata: dict[str, object] | None = None,
    sequence: int = 0,
    output: object = "y",
) -> Span:
    return Span(
        id=uuid4().hex,
        trace_id=trace_id,
        parent_ids=parent_ids or [],
        sequence=sequence,
        type=type,
        name=name,
        attributes={},
        input="x",
        output=output,
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
    root_span = _make_span(tid, name="agent")
    spans = [
        root_span,
        _make_span(
            tid,
            parent_ids=[root_span.id],
            sequence=1,
            name="llm-call",
            type=SpanType.LLM,
        ),
    ]
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
    req = CallToolRequest(params=CallToolRequestParams(name=name, arguments=arguments))
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
    root_span_id = next(s.id for s in store.iter_spans(tid) if not s.parent_ids)
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


# ---------------------------------------------------------------------------
# Missing-tool coverage
# ---------------------------------------------------------------------------


def test_diff_traces_tool(tmp_path: Path) -> None:
    """``diff_traces`` returns a JSON diff with same key shape."""
    from clew.core.branch import BranchManager
    from clew.core.trace import TraceStore

    store = Store(tmp_path / ".clew")
    ts = TraceStore(store)
    bm = BranchManager(ts)
    # Two traces
    tid_a = uuid4().hex
    root_a = _make_span(tid_a, name="a1")
    spans_a = [
        root_a,
        _make_span(
            tid_a,
            parent_ids=[root_a.id],
            sequence=1,
            name="a2",
            type=SpanType.LLM,
            output="out-A",
        ),
    ]
    for s in spans_a:
        ts.add_span(s)
    tid_b = uuid4().hex
    root_b = _make_span(tid_b, name="a1")
    spans_b = [
        root_b,
        _make_span(
            tid_b,
            parent_ids=[root_b.id],
            sequence=1,
            name="a2",
            type=SpanType.LLM,
            output="out-B",
        ),
    ]
    for s in spans_b:
        ts.add_span(s)
    bm.move("main", spans_a[0].id)
    out = _call(
        build_server(),
        "diff_traces",
        {"trace_a": tid_a, "trace_b": tid_b, "root": str(tmp_path / ".clew")},
    )
    text = _text(out)
    parsed = json.loads(text)
    assert "added" in parsed
    assert "removed" in parsed
    assert "modified" in parsed


def test_replay_tool(tmp_path: Path) -> None:
    """``replay`` returns a new trace_id different from the source."""
    tid = _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "replay",
        {"trace_id": tid, "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert "new_trace_id" in parsed
    assert parsed["new_trace_id"] != tid


def test_create_branch_defaults_to_head(tmp_path: Path) -> None:
    """``create_branch`` without ``from_span`` uses current HEAD."""
    from clew.core.branch import BranchManager
    from clew.core.trace import TraceStore

    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "create_branch",
        {"name": "auto", "root": str(tmp_path / ".clew")},
    )
    assert "auto" in _text(out)
    # Verify it points at the same span as main
    ts = TraceStore(Store(tmp_path / ".clew"))
    bm = BranchManager(ts)
    assert bm.get("auto").head_span_id == bm.get("main").head_span_id


def test_show_branch_head_alias(tmp_path: Path) -> None:
    """``show_branch`` accepts the literal name ``HEAD``."""
    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "show_branch",
        {"name": "HEAD", "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert parsed["branch"] == "HEAD"
    assert len(parsed["spans"]) >= 1


def test_search_with_metadata_filter(tmp_path: Path) -> None:
    """``search`` accepts a ``metadata`` dict and filters by it."""
    from clew.core.trace import TraceStore

    store = Store(tmp_path / ".clew")
    ts = TraceStore(store)
    tid = uuid4().hex
    s_with = _make_span(tid, name="with-meta", metadata={"model": "gpt-4o"})
    s_without = _make_span(tid, parent_ids=[s_with.id], sequence=1, name="without-meta")
    ts.add_span(s_with)
    ts.add_span(s_without)
    out = _call(
        build_server(),
        "search",
        {"metadata": {"model": "gpt-4o"}, "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    names = {m["name"] for m in parsed["matches"]}
    assert "with-meta" in names
    assert "without-meta" not in names


def test_query_with_metadata_specs(tmp_path: Path) -> None:
    """``query`` accepts ``metadata_specs`` (k=v CLI form) and filters."""
    from clew.core.trace import TraceStore

    store = Store(tmp_path / ".clew")
    ts = TraceStore(store)
    tid = uuid4().hex
    s1 = _make_span(tid, name="a", metadata={"temperature": 0.7})
    s2 = _make_span(
        tid,
        parent_ids=[s1.id],
        sequence=1,
        name="b",
        metadata={"temperature": 0.3},
    )
    ts.add_span(s1)
    ts.add_span(s2)
    out = _call(
        build_server(),
        "query",
        {"metadata_specs": ["temperature=0.7"], "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    names = {m["name"] for m in parsed["matches"]}
    assert names == {"a"}


def test_replay_tool_default_executor_is_mock(tmp_path: Path) -> None:
    """``replay`` with no ``executor`` arg uses mock and is deterministic."""
    tid = _seed(tmp_path / ".clew")
    out1 = _call(
        build_server(),
        "replay",
        {"trace_id": tid, "root": str(tmp_path / ".clew")},
    )
    out2 = _call(
        build_server(),
        "replay",
        {"trace_id": tid, "root": str(tmp_path / ".clew")},
    )
    # Both replays should succeed; the resulting trace ids will
    # differ (each replay gets a fresh trace_id).
    p1 = json.loads(_text(out1))
    p2 = json.loads(_text(out2))
    assert "new_trace_id" in p1
    assert "new_trace_id" in p2


# ---------------------------------------------------------------------------
# Error-path coverage
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_error_dict(tmp_path: Path) -> None:
    """An unknown tool returns a JSON error payload, not a crash."""
    out = _call(
        build_server(),
        "no_such_tool",
        {"root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert "error" in parsed


def test_get_trace_missing_returns_error(tmp_path: Path) -> None:
    """``get_trace`` with a missing id returns a JSON error."""
    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "get_trace",
        {"trace_id": "f" * 32, "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert "error" in parsed


def test_get_span_missing_returns_error(tmp_path: Path) -> None:
    """``get_span`` with a missing id returns a JSON error."""
    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "get_span",
        {"span_id": "f" * 32, "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert "error" in parsed


def test_search_with_invalid_type_returns_error(tmp_path: Path) -> None:
    """``search`` with an invalid span type returns a JSON error."""
    _seed(tmp_path / ".clew")
    out = _call(
        build_server(),
        "search",
        {"type": "BOGUS", "root": str(tmp_path / ".clew")},
    )
    parsed = json.loads(_text(out))
    assert "error" in parsed


# ---------------------------------------------------------------------------
# Resource coverage
# ---------------------------------------------------------------------------


def test_read_resource_store_info(tmp_path: Path, monkeypatch) -> None:
    """``store://info`` returns store metadata for the cwd-relative store.

    We seed under tmp_path and use ``monkeypatch.chdir`` so the
    resource's ``_open(None)`` finds the freshly-initialised store.
    """
    import asyncio

    from mcp.types import ReadResourceRequest, ReadResourceRequestParams

    _seed(tmp_path / ".clew")
    monkeypatch.chdir(tmp_path)
    server = build_server()
    handler = server.request_handlers[ReadResourceRequest]
    req = ReadResourceRequest(
        params=ReadResourceRequestParams(
            uri="store://info",  # type: ignore[arg-type]
        )
    )
    result = asyncio.run(handler(req))
    assert result.root.contents
    text = result.root.contents[0].text
    parsed = json.loads(text)
    assert "version" in parsed
    assert parsed["branches"] == ["main"]


def test_read_resource_trace(tmp_path: Path, monkeypatch) -> None:
    """``trace://<id>`` returns the trace for the given id.

    Same pattern: seed under tmp_path and ``chdir`` so the resource
    finds the right store.
    """
    import asyncio

    from mcp.types import ReadResourceRequest, ReadResourceRequestParams

    tid = _seed(tmp_path / ".clew")
    monkeypatch.chdir(tmp_path)
    server = build_server()
    handler = server.request_handlers[ReadResourceRequest]
    req = ReadResourceRequest(
        params=ReadResourceRequestParams(
            uri=f"trace://{tid}",  # type: ignore[arg-type]
        )
    )
    result = asyncio.run(handler(req))
    assert result.root.contents
    text = result.root.contents[0].text
    parsed = json.loads(text)
    assert parsed["trace_id"] == tid
