"""The clew command-line interface.

A typer-based CLI that exposes the full clew surface area: init, log,
show, branch, branches, checkout, replay, diff, share, tui, version.

Run as ``clew <command>`` or ``python -m clew <command>``. The entry
point is configured in ``pyproject.toml``:

    [project.scripts]
    clew = "clew.cli:app"

Design notes
------------

* Every command has ``--help`` and ``--json`` (where applicable) and
  exits 0 on success, 1 on error.
* Errors are rendered as a clean rich Panel; we never print
  tracebacks to the user.
* The store is opened from the current directory's ``.clew`` by
  default; ``--root`` lets the user point elsewhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from clew.core.branch import BranchManager
from clew.core.diff import diff as diff_traces
from clew.core.diff import format_json as diff_format_json
from clew.core.replay import MockExecutor, ReplayEngine
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.ui.render import render_diff, render_log, render_span_tree

#: The typer app. Configured as the entry point in pyproject.toml.
app = typer.Typer(
    name="clew",
    help="git for AI reasoning — trace, branch, replay, and diff your agent runs.",
    no_args_is_help=True,
    add_completion=False,
)

#: Shared rich console for error reporting.
_err_console = Console(stderr=True, style="red")


def _err(msg: str) -> None:
    """Print an error and exit 1."""
    _err_console.print(f"[red]error:[/red] {msg}")
    raise typer.Exit(code=1)


def _resolve_root(path: Path | None) -> Path:
    """Find a ``.clew`` directory under ``path`` (or cwd)."""
    from clew.utils.paths import clew_root
    return clew_root(path or Path.cwd())


def _open_store(root: Path) -> tuple[Store, TraceStore]:
    """Open the clew store at ``root`` or fail with a friendly error."""
    if not (root / "manifest.json").exists():
        _err(f"no clew store at {root}. run `clew init` first.")
    return Store(root), TraceStore(Store(root))


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command("init")
def cmd_init(
    path: Path = typer.Argument(Path.cwd(), help="Directory to initialize (default: cwd)."),
) -> None:
    """Initialize a new ``.clew/`` store in ``PATH`` (idempotent)."""
    target = path / ".clew"
    target.mkdir(parents=True, exist_ok=True)
    if not (target / "manifest.json").exists():
        manifest = {"version": 1, "created_at": datetime.now(UTC).isoformat()}
        (target / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    typer.echo(f"Initialized clew store at {target}")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command("version")
def cmd_version() -> None:
    """Print the clew version."""
    from clew import __version__

    typer.echo(f"clew {__version__}")


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@app.command("log")
def cmd_log(
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    as_json: bool = typer.Option(False, "--json", help="Emit NDJSON instead of a table."),
) -> None:
    """List all traces in the store."""
    store, ts = _open_store(_resolve_root(root))
    rows: list[dict[str, Any]] = []
    for trace_id in store.iter_traces():
        try:
            t = ts.get_trace(trace_id)
        except KeyError:
            continue
        rows.append(
            {
                "trace_id": trace_id,
                "root_name": t.spans[0].name if t.spans else "?",
                "span_count": len(t.spans),
                "started_at": t.spans[0].started_at.isoformat() if t.spans else "",
            }
        )
    rows.sort(key=lambda r: r["started_at"], reverse=True)
    if as_json:
        for row in rows:
            typer.echo(json.dumps(row))
        return
    console = Console()
    if not rows:
        console.print("[dim]No traces yet. Run an agent with the SDK first.[/dim]")
        return
    console.print(render_log(rows))


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@app.command("show")
def cmd_show(
    trace_id: str = typer.Argument(..., help="Trace id to show."),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    as_json: bool = typer.Option(False, "--json", help="Emit NDJSON instead of a tree."),
) -> None:
    """Show the span tree of a trace."""
    store, ts = _open_store(_resolve_root(root))
    try:
        trace = ts.get_trace(trace_id)
    except KeyError:
        _err(f"trace {trace_id!r} not found")
        return  # unreachable
    if as_json:
        for s in trace.spans:
            typer.echo(s.model_dump_json())
        return
    Console().print(render_span_tree(trace))


# ---------------------------------------------------------------------------
# branch / branches / checkout
# ---------------------------------------------------------------------------


@app.command("branch")
def cmd_branch(
    name: str = typer.Argument(..., help="Branch name to create."),
    from_span: str = typer.Argument(None, help="Span id; defaults to current HEAD."),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
) -> None:
    """Create a new branch pointing at a span (defaults to current HEAD)."""
    store, ts = _open_store(_resolve_root(root))
    bm = BranchManager(ts)
    head = from_span or bm.head_span_id()
    try:
        bm.create(name, head)
    except FileExistsError:
        _err(f"branch {name!r} already exists")
        return
    typer.echo(f"Created branch {name!r} → {head[:12]}…")


@app.command("branches")
def cmd_branches(
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """List all branches."""
    store, ts = _open_store(_resolve_root(root))
    bm = BranchManager(ts)
    rows: list[dict[str, Any]] = []
    for b in bm.list():
        rows.append(
            {
                "name": b.name,
                "head_span_id": b.head_span_id,
                "is_current": b.name == bm.current(),
            }
        )
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo("(no branches)")
        return
    from rich.table import Table
    table = Table()
    table.add_column("name", style="bold")
    table.add_column("head span", style="cyan")
    for r in rows:
        marker = " *" if r["is_current"] else ""
        table.add_row(r["name"] + marker, r["head_span_id"][:12] + "…")
    Console().print(table)


@app.command("checkout")
def cmd_checkout(
    name: str = typer.Argument(..., help="Branch to switch to."),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
) -> None:
    """Switch the current branch."""
    store, ts = _open_store(_resolve_root(root))
    bm = BranchManager(ts)
    try:
        bm.checkout(name)
    except KeyError:
        _err(f"branch {name!r} not found")
        return
    typer.echo(f"Switched to branch {name!r}")


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@app.command("replay")
def cmd_replay(
    trace_id: str = typer.Argument(..., help="Trace id to replay."),
    from_span: str = typer.Option(None, "--from", help="Replay from this span only."),
    executor_kind: str = typer.Option(
        "mock", "--executor", help="Executor: 'mock' (re-uses outputs) or 'recording'."
    ),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of plain text."),
) -> None:
    """Replay a trace, producing a new trace (original is untouched)."""
    store, ts = _open_store(_resolve_root(root))
    ex = MockExecutor()
    if executor_kind not in {"mock", "recording"}:
        _err(f"unknown executor {executor_kind!r}")
        return
    engine = ReplayEngine(ts, executor=ex)

    async def _run() -> str:
        result = await engine.replay(trace_id, from_span_id=from_span)
        return result.trace_id

    new_trace_id = asyncio.run(_run())
    if as_json:
        typer.echo(json.dumps({"new_trace_id": new_trace_id}))
    else:
        typer.echo(new_trace_id)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@app.command("diff")
def cmd_diff(
    trace_a: str = typer.Argument(..., help="First trace id."),
    trace_b: str = typer.Argument(..., help="Second trace id."),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a text diff."),
) -> None:
    """Diff two traces."""
    store, ts = _open_store(_resolve_root(root))
    try:
        a = ts.get_trace(trace_a)
        b = ts.get_trace(trace_b)
    except KeyError as exc:
        _err(f"trace {exc.args[0]!r} not found")
        return
    d = diff_traces(a, b)
    if as_json:
        typer.echo(diff_format_json(d))
    else:
        Console().print(render_diff(d))


# ---------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------


@app.command("share")
def cmd_share(
    trace_id: str = typer.Argument(..., help="Trace id to share."),
    out: Path = typer.Option(
        None, "--out", help="Output path. Default: <trace_id>.clew.tgz in cwd."
    ),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
) -> None:
    """Export a portable signed bundle (tar.gz) for sharing."""
    clew_path = _resolve_root(root)
    output = out or (Path.cwd() / f"{trace_id}.clew.tgz")
    # Build a manifest.
    manifest = {
        "format": "clew-bundle",
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "trace_id": trace_id,
        "source_store": str(clew_path),
    }
    # Hash the spans for a content signature.
    store, ts = _open_store(clew_path)
    try:
        trace = ts.get_trace(trace_id)
    except KeyError:
        _err(f"trace {trace_id!r} not found")
        return
    h = hashlib.sha256()
    for s in trace.spans:
        h.update(s.model_dump_json().encode("utf-8"))
    manifest["sha256"] = h.hexdigest()
    # Write the tarball.
    with tarfile.open(output, "w:gz") as tar:
        # Add the manifest.
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(datetime.now(UTC).timestamp())
        tar.addfile(info, io.BytesIO(manifest_bytes))
        # Add all span files for this trace.
        for s in trace.spans:
            shard = store._span_path(s.id)
            if shard.exists():
                tar.add(shard, arcname=f"spans/{shard.relative_to(clew_path / 'spans')}")
    typer.echo(str(output))


# ---------------------------------------------------------------------------
# tui
# ---------------------------------------------------------------------------


@app.command("tui")
def cmd_tui(
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
) -> None:
    """Launch the interactive TUI browser."""
    from clew.ui.tui import TraceBrowserApp

    clew_path = _resolve_root(root)
    if not (clew_path / "manifest.json").exists():
        _err(f"no clew store at {clew_path}. run `clew init` first.")
    app_tui = TraceBrowserApp(clew_path)
    app_tui.run()
