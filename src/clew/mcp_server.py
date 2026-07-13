"""Model Context Protocol (MCP) server for clew.

Expose a clew store to any MCP-compatible client: Claude Desktop,
Cursor, Cline, the MCP Inspector, etc. Once connected, the host
LLM can browse, search, branch, replay, and diff your agent
traces — directly from the conversation.

Tools exposed
-------------
- ``list_traces``           Enumerate every trace in the store.
- ``get_trace``             Fetch a single trace by id.
- ``get_span``              Fetch a single span by id.
- ``search``                Search by name/type/status/metadata.
- ``list_branches``         Show every branch and its head span.
- ``show_branch``           Show the span tree rooted at a branch.
- ``diff_traces``           Compute a structural diff between two traces.
- ``create_branch``         Create a new branch at a given span.
- ``checkout``              Switch the current branch.
- ``replay``                Replay a trace, returning the new trace id.
- ``doctor``                Run clew doctor and return the report.
- ``query``                 Run clew query with a filter spec.

Resources exposed
-----------------
- ``trace://<id>``          A single trace as a JSON document.
- ``store://info``          A summary of the store (head, branches, count).

The server is started with ``clew mcp``. It speaks JSON-RPC over
stdin/stdout, the standard MCP transport.

Install: ``uv add 'clew[mcp]'`` (or ``uv add clew`` then
``uv add mcp``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

from clew.core.branch import BranchManager
from clew.core.diff import diff as diff_traces
from clew.core.diff import format_json as diff_format_json
from clew.core.health import check_store
from clew.core.models import Span, SpanStatus, SpanType
from clew.core.query import QueryFilter, parse_metadata_spec, query
from clew.core.replay import MockExecutor, ReplayEngine
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.utils.paths import clew_root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open(root: str | None) -> tuple[Store, TraceStore, Path]:
    """Open the store at the given root (or find one upward from cwd)."""
    cwd = Path(root) if root else Path.cwd()
    root_path = clew_root(cwd)
    if not (root_path / "manifest.json").exists():
        raise FileNotFoundError(f"no clew store at {root_path}; run `clew init` first")
    return Store(root_path), TraceStore(Store(root_path)), root_path


def _span_to_dict(s: Span) -> dict[str, Any]:
    """Convert a Span to a JSON-safe dict."""
    return {
        "id": s.id,
        "trace_id": s.trace_id,
        "parent_ids": list(s.parent_ids),
        "type": s.type.value,
        "name": s.name,
        "attributes": dict(s.attributes),
        "input": s.input,
        "output": s.output,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "status": s.status.value,
        "error": s.error,
        "metadata": dict(s.metadata) if s.metadata else None,
    }


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def build_server() -> Server:
    """Construct an MCP :class:`Server` exposing clew tools."""
    server: Server = Server("clew")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_traces",
                description="Enumerate every trace in the clew store. "
                "Returns an array of {trace_id, root_span_id, span_count, started_at}.",
                inputSchema={
                    "type": "object",
                    "properties": {"root": {"type": "string", "description": "Path to the .clew directory (optional). Defaults to the nearest one upward from cwd."}},
                    "required": [],
                },
            ),
            Tool(
                name="get_trace",
                description="Fetch a single trace by id. Returns the full span tree.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "trace_id": {"type": "string"},
                        "root": {"type": "string"},
                    },
                    "required": ["trace_id"],
                },
            ),
            Tool(
                name="get_span",
                description="Fetch a single span by id.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "span_id": {"type": "string"},
                        "root": {"type": "string"},
                    },
                    "required": ["span_id"],
                },
            ),
            Tool(
                name="search",
                description="Search spans by name substring, type, status, or metadata key=value. Returns matching spans.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Substring (case-insensitive) match on span name."},
                        "type": {"type": "string", "description": "Span type (LLM, TOOL, DECISION, OBSERVATION)."},
                        "status": {"type": "string", "description": "Span status (OK, ERROR)."},
                        "trace_id": {"type": "string"},
                        "metadata": {"type": "object", "description": "Metadata filter as {key: value}."},
                        "limit": {"type": "integer", "default": 50},
                        "root": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="list_branches",
                description="List all branches and their head span ids.",
                inputSchema={
                    "type": "object",
                    "properties": {"root": {"type": "string"}},
                },
            ),
            Tool(
                name="show_branch",
                description="Show the span tree rooted at a branch's HEAD.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Branch name. Use 'HEAD' for the current branch."},
                        "root": {"type": "string"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="diff_traces",
                description="Compute a structural diff between two traces. Returns the diff as JSON.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "trace_a": {"type": "string"},
                        "trace_b": {"type": "string"},
                        "root": {"type": "string"},
                    },
                    "required": ["trace_a", "trace_b"],
                },
            ),
            Tool(
                name="create_branch",
                description="Create a new branch pointing at a span. Returns the new branch name.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "from_span": {"type": "string", "description": "Span id to branch from. Defaults to current HEAD."},
                        "root": {"type": "string"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="checkout",
                description="Switch the current branch (HEAD) to a named branch.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "root": {"type": "string"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="replay",
                description="Replay a trace, producing a new trace id. Uses the MockExecutor by default (deterministic).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "trace_id": {"type": "string"},
                        "executor": {"type": "string", "enum": ["mock", "recording"], "default": "mock"},
                        "root": {"type": "string"},
                    },
                    "required": ["trace_id"],
                },
            ),
            Tool(
                name="doctor",
                description="Run clew doctor on the store and return a structured health report.",
                inputSchema={
                    "type": "object",
                    "properties": {"root": {"type": "string"}},
                },
            ),
            Tool(
                name="query",
                description="Run clew query with a filter spec. Returns matching spans with provenance.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "status": {"type": "string"},
                        "trace_id": {"type": "string"},
                        "metadata_specs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of 'k=v' strings (parsed as JSON where possible).",
                        },
                        "limit": {"type": "integer", "default": 50},
                        "root": {"type": "string"},
                    },
                },
            ),
        ]

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        # The list of resources is dynamic (depends on the store),
        # but for simplicity we expose a single store://info resource.
        # Trace-specific resources are listed on demand via the
        # get_trace tool.
        return [
            Resource(
                uri="store://info",
                name="clew store info",
                description="Summary of the active clew store (head, branches, counts).",
                mimeType="application/json",
            )
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        if uri == "store://info":
            try:
                _, ts, root = _open(None)
            except FileNotFoundError as exc:
                return json.dumps({"error": str(exc)})
            bm = BranchManager(ts)
            head = bm.head_span_id() if (root / "HEAD").exists() else None
            return json.dumps(
                {
                    "root": str(root),
                    "head": head,
                    "branches": list(bm.list()),
                    "trace_count": sum(1 for _ in ts.store.iter_traces()),
                },
                default=str,
                indent=2,
            )
        if uri.startswith("trace://"):
            tid = uri[len("trace://") :]
            try:
                _, ts, _ = _open(None)
                trace = ts.get_trace(tid)
            except (KeyError, FileNotFoundError) as exc:
                return json.dumps({"error": str(exc)})
            return json.dumps(
                {
                    "trace_id": trace.trace_id,
                    "root_span_id": trace.root_span_id,
                    "spans": [_span_to_dict(s) for s in trace.spans],
                },
                default=str,
                indent=2,
            )
        return json.dumps({"error": f"unknown resource: {uri}"})

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            if name == "list_traces":
                store, ts, _ = _open(arguments.get("root"))
                traces = []
                for tid in store.iter_traces():
                    try:
                        trace = ts.get_trace(tid)
                    except KeyError:
                        continue
                    root_span = next(
                        (s for s in trace.spans if s.id == trace.root_span_id),
                        None,
                    )
                    traces.append(
                        {
                            "trace_id": tid,
                            "root_span_id": trace.root_span_id,
                            "span_count": len(trace.spans),
                            "started_at": (
                                root_span.started_at.isoformat()
                                if root_span
                                else None
                            ),
                            "root_name": root_span.name if root_span else None,
                        }
                    )
                return [TextContent(type="text", text=json.dumps(traces, indent=2))]

            if name == "get_trace":
                _, ts, _ = _open(arguments.get("root"))
                tid = arguments["trace_id"]
                trace = ts.get_trace(tid)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "trace_id": trace.trace_id,
                                "root_span_id": trace.root_span_id,
                                "spans": [_span_to_dict(s) for s in trace.spans],
                            },
                            default=str,
                            indent=2,
                        ),
                    )
                ]

            if name == "get_span":
                store, _, _ = _open(arguments.get("root"))
                span = store.get(arguments["span_id"])
                return [TextContent(type="text", text=json.dumps(_span_to_dict(span), default=str, indent=2))]

            if name == "search":
                _, ts, root = _open(arguments.get("root"))
                type_enum = SpanType(arguments["type"]) if arguments.get("type") else None
                status_enum = (
                    SpanStatus(arguments["status"]) if arguments.get("status") else None
                )
                meta = arguments.get("metadata") or None
                filt = QueryFilter(
                    name=arguments.get("name"),
                    type=type_enum,
                    status=status_enum,
                    trace_id=arguments.get("trace_id"),
                    metadata=meta,
                    limit=arguments.get("limit", 50),
                )
                results = query(root, filt)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "count": len(results),
                                "matches": [
                                    {
                                        **_span_to_dict(r.span),
                                        "trace_id": r.trace_id,
                                        "root_span_id": r.root_span_id,
                                    }
                                    for r in results
                                ],
                            },
                            default=str,
                            indent=2,
                        ),
                    )
                ]

            if name == "list_branches":
                _, ts, _ = _open(arguments.get("root"))
                bm = BranchManager(ts)
                head_id = None
                head = (ts.store.root / "HEAD")
                if head.exists():
                    try:
                        head_id = bm.head_span_id()
                    except Exception:
                        head_id = None
                rows = [
                    {
                        "name": b.name,
                        "head_span_id": b.head_span_id,
                        "is_current": b.head_span_id == head_id,
                    }
                    for b in bm.list()
                ]
                return [TextContent(type="text", text=json.dumps(rows, indent=2))]

            if name == "show_branch":
                _, ts, _ = _open(arguments.get("root"))
                bm = BranchManager(ts)
                branch_name = arguments["name"]
                span_id = bm.head_span_id() if branch_name == "HEAD" else bm._read_ref(branch_name)
                span = ts.store.get(span_id)
                spans = [*list(ts.descendants(span_id)), span]
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "branch": branch_name,
                                "head_span_id": span_id,
                                "spans": [_span_to_dict(s) for s in spans],
                            },
                            default=str,
                            indent=2,
                        ),
                    )
                ]

            if name == "diff_traces":
                _, ts, _ = _open(arguments.get("root"))
                a = ts.get_trace(arguments["trace_a"])
                b = ts.get_trace(arguments["trace_b"])
                d = diff_traces(a, b)
                return [TextContent(type="text", text=diff_format_json(d))]

            if name == "create_branch":
                _, ts, _ = _open(arguments.get("root"))
                bm = BranchManager(ts)
                bm.create(
                    arguments["name"],
                    arguments.get("from_span") or bm.head_span_id(),
                )
                return [TextContent(type="text", text=f"created branch {arguments['name']!r}")]

            if name == "checkout":
                _, ts, _ = _open(arguments.get("root"))
                BranchManager(ts).checkout(arguments["name"])
                return [TextContent(type="text", text=f"checked out {arguments['name']!r}")]

            if name == "replay":
                _, ts, _ = _open(arguments.get("root"))
                trace = ts.get_trace(arguments["trace_id"])
                arguments.get("executor", "mock")
                executor = MockExecutor()
                engine = ReplayEngine(ts, executor=executor)
                # Replay is async-aware but mock executor is sync; the
                # sync path is fine for our purposes.
                import asyncio

                new_trace_id = asyncio.run(
                    engine.replay(
                        trace.root_span_id,
                        from_span_id=None,
                        executor=executor,
                    )
                )
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"new_trace_id": new_trace_id}),
                    )
                ]

            if name == "doctor":
                _, _, root = _open(arguments.get("root"))
                r = check_store(root)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "healthy": r.healthy,
                                "head": r.head,
                                "branches": list(r.branches),
                                "span_files": r.span_files,
                                "indexed_spans": r.indexed_spans,
                                "ref_count": r.ref_count,
                                "errors": [i.to_dict() for i in r.errors],
                                "warnings": [i.to_dict() for i in r.warnings],
                            },
                            indent=2,
                        ),
                    )
                ]

            if name == "query":
                _, ts, root = _open(arguments.get("root"))
                type_enum = (
                    SpanType(arguments["type"]) if arguments.get("type") else None
                )
                status_enum = (
                    SpanStatus(arguments["status"])
                    if arguments.get("status")
                    else None
                )
                meta = None
                if arguments.get("metadata_specs"):
                    meta = parse_metadata_spec(arguments["metadata_specs"])
                filt = QueryFilter(
                    name=arguments.get("name"),
                    type=type_enum,
                    status=status_enum,
                    trace_id=arguments.get("trace_id"),
                    metadata=meta,
                    limit=arguments.get("limit", 50),
                )
                results = query(root, filt)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "count": len(results),
                                "matches": [
                                    {
                                        **_span_to_dict(r.span),
                                        "trace_id": r.trace_id,
                                    }
                                    for r in results
                                ],
                            },
                            default=str,
                            indent=2,
                        ),
                    )
                ]

            return [TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}))]

        except Exception as exc:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
                )
            ]

    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the MCP server on stdio. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="clew-mcp",
        description="Run the clew MCP server (stdio transport).",
    )
    parser.parse_args(argv)

    async def _run() -> None:
        server = build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
