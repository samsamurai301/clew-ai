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
import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console

from clew.core.branch import BranchManager
from clew.core.bundle import (
    build_bundle,
    extract_spans,
    generate_keypair,
    load_private_key,
    load_public_key,
    verify_bundle,
)
from clew.core.diff import diff as diff_traces
from clew.core.diff import format_json as diff_format_json
from clew.core.format import read_ndjson, write_ndjson
from clew.core.health import check_store, gc
from clew.core.html_report import write_html
from clew.core.models import SpanStatus, SpanType
from clew.core.query import QueryFilter, parse_metadata_spec, query
from clew.core.replay import MockExecutor, ReplayEngine
from clew.core.runner import run_and_record
from clew.core.store import Store
from clew.core.trace import TraceStore
from clew.ui.render import render_diff, render_log, render_span_tree


def _version_callback(value: bool) -> None:
    """Print the clew version and exit (--version flag)."""
    if value:
        from clew import __version__

        typer.echo(f"clew {__version__}")
        raise typer.Exit()


#: The typer app. Configured as the entry point in pyproject.toml.
app = typer.Typer(
    name="clew",
    help="git for AI reasoning — trace, branch, replay, and diff your agent runs.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print the clew version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Top-level options shared by every subcommand."""

#: Shared rich console for error reporting.
_err_console = Console(stderr=True, style="red")


def _err(msg: str) -> NoReturn:
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
# trace (subprocess recorder)
# ---------------------------------------------------------------------------


@app.command("trace")
def cmd_trace(
    argv: list[str] = typer.Argument(
        ...,
        help="Command to run, e.g. `clew trace -- python my_agent.py`. "
        "Everything after `--` is the command. "
        "The command's argv, exit code, and tail of stdout/stderr "
        "are recorded in the resulting span — do not pass commands "
        "that read or print secrets.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Span name. Default: basename of argv[0].",
    ),
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        help="Max seconds to wait before killing the process.",
    ),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
) -> None:
    """Run a subprocess and record it as a single span.

    Useful for one-off agents that don't import clew. The command
    itself runs from the current working directory (not the .clew
    dir); use ``cd`` in the shell if you need to change dirs.

    Example:

        clew trace -- python my_agent.py
    """
    if not argv:
        _err("`clew trace` requires a command after `--`")
    clew_path = _resolve_root(root)
    store = Store(clew_path)
    span = run_and_record(
        argv,
        cwd=Path.cwd(),
        store=store,
        name=name,
        timeout_s=timeout,
    )
    typer.echo(span.id)


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
    html: Path = typer.Option(
        None,
        "--html",
        help="Write a self-contained interactive HTML report. "
        "Default: <trace_id>.html in cwd.",
    ),
) -> None:
    """Show the span tree of a trace.

    With ``--html <path>``, writes a self-contained HTML page
    (dark theme, collapsible spans, ERROR highlights) suitable
    for sharing via email or gist.
    """
    store, ts = _open_store(_resolve_root(root))
    try:
        trace = ts.get_trace(trace_id)
    except KeyError:
        _err(f"trace {trace_id!r} not found")
        raise  # mypy can't prove _err is NoReturn
    if html is not None:
        write_html(trace, html)
        typer.echo(str(html))
        return
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
    d = diff_traces(a, b)
    if as_json:
        typer.echo(diff_format_json(d))
    else:
        Console().print(render_diff(d))


# ---------------------------------------------------------------------------
# share / verify / import  (signed bundles)
# ---------------------------------------------------------------------------


@app.command("keygen")
def cmd_keygen(
    out: Path = typer.Option(
        Path("clew-key.pem"), "--out", help="Output path for the private key (PEM)."
    ),
    public_out: Path | None = typer.Option(
        None,
        "--public-out",
        help="Output path for the public key (PEM). Default: <out>.pub",
    ),
) -> None:
    """Generate a fresh Ed25519 keypair for signing bundles.

    The private key is written UNENCRYPTED. Treat it like a password:
    put it in your password manager, never commit it. The public key
    is what you share with verifiers.
    """
    priv_pem, pub_pem = generate_keypair()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(priv_pem)
    with contextlib.suppress(OSError):
        out.chmod(0o600)
    pub_path = public_out or out.with_suffix(out.suffix + ".pub")
    pub_path.write_bytes(pub_pem)
    with contextlib.suppress(OSError):
        pub_path.chmod(0o644)
    typer.echo(f"private key: {out}  (keep this secret)")
    typer.echo(f"public  key: {pub_path}")


@app.command("share")
def cmd_share(
    trace_id: str = typer.Argument(..., help="Trace id to share."),
    out: Path = typer.Option(
        None, "--out", help="Output path. Default: <trace_id>.clew.tgz in cwd."
    ),
    key: Path = typer.Option(
        ..., "--key", help="Ed25519 private key (PEM) to sign with."
    ),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
) -> None:
    """Export a portable signed bundle (tar.gz) for sharing.

    The bundle is signed with Ed25519 over the manifest. Anyone with
    the matching public key can verify it has not been tampered with
    in transit. Bundle layout:

        manifest.json   bundle metadata + content hash
        sig             64-byte Ed25519 signature
        spans/<id>.json one JSON-Lines file per span
    """
    clew_path = _resolve_root(root)
    output = out or (Path.cwd() / f"{trace_id}.clew.tgz")
    store, ts = _open_store(clew_path)
    try:
        trace = ts.get_trace(trace_id)
    except KeyError:
        _err(f"trace {trace_id!r} not found")
    priv = load_private_key(key)
    pub = priv.public_key()
    result = build_bundle(
        trace,
        trace.spans,
        out=output,
        source_store=clew_path,
        private_key=priv,
        public_key=pub,
    )
    typer.echo(str(result.path))


@app.command("verify")
def cmd_verify(
    bundle: Path = typer.Argument(..., help="Path to the .clew.tgz bundle."),
    public_key: Path = typer.Option(
        ..., "--public-key", help="Path to the signer's Ed25519 public key (PEM)."
    ),
) -> None:
    """Verify a signed bundle. Exits 0 on success, 1 on tamper or format error."""
    pub = load_public_key(public_key)
    v = verify_bundle(bundle, pub)
    if not v.valid:
        _err(f"bundle invalid: {v.reason}")
    assert v.manifest is not None
    typer.echo(
        f"valid  trace_id={v.manifest['trace_id']}  "
        f"spans={len(v.span_files)}  "
        f"created_at={v.manifest['created_at']}"
    )


@app.command("import")
def cmd_import(
    bundle: Path = typer.Argument(..., help="Path to the .clew.tgz bundle."),
    public_key: Path = typer.Option(
        ..., "--public-key", help="Path to the signer's Ed25519 public key (PEM)."
    ),
    branch_name: str | None = typer.Option(
        None,
        "--branch",
        help="Create a branch pointing at the imported root span. "
        "Default: don't create a branch (spans are added to the store only).",
    ),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
) -> None:
    """Verify and import a signed bundle into the local store.

    Existing spans with the same id are left untouched (import is
    idempotent). A branch is optionally created pointing at the
    imported root span so you can `clew checkout` into it.
    """
    pub = load_public_key(public_key)
    v = verify_bundle(bundle, pub)
    if not v.valid:
        _err(f"bundle invalid: {v.reason}")
    assert v.manifest is not None
    spans = extract_spans(bundle)
    clew_path = _resolve_root(root)
    store, ts = _open_store(clew_path)
    added = 0
    for s in spans.values():
        try:
            ts.add_span(s)
            added += 1
        except Exception as exc:
            typer.echo(f"  warn: failed to import span {s.id}: {exc}", err=True)
    if branch_name and v.manifest.get("root_span_id"):
        bm = BranchManager(ts)
        bm.create(branch_name, v.manifest["root_span_id"])
    typer.echo(
        f"imported {added}/{len(spans)} spans, "
        f"trace_id={v.manifest['trace_id']}"
    )


# ---------------------------------------------------------------------------
# export / otel-import  (NDJSON)
# ---------------------------------------------------------------------------


@app.command("export")
def cmd_export(
    trace_id: str = typer.Argument(..., help="Trace id to export."),
    out: Path = typer.Option(
        None, "--out", help="Output path. Default: <trace_id>.clew.ndjson in cwd."
    ),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
) -> None:
    """Export a trace to OTel-compatible NDJSON.

    The output is one JSON object per line: a leading ``_kind:
    trace`` header followed by every span rendered in OTel's
    gen_ai.* shape. The file is round-trippable through ``clew
    otel-import`` or any OTel collector that accepts NDJSON.
    """
    clew_path = _resolve_root(root)
    store, ts = _open_store(clew_path)
    try:
        trace = ts.get_trace(trace_id)
    except KeyError:
        _err(f"trace {trace_id!r} not found")
    output = out or (Path.cwd() / f"{trace_id}.clew.ndjson")
    n = write_ndjson(output, trace.trace_id, trace.spans)
    typer.echo(f"wrote {n} spans to {output}")


@app.command("otel-import")
def cmd_otel_import(
    ndjson: Path = typer.Argument(..., help="Path to the .ndjson file."),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    branch_name: str | None = typer.Option(
        None,
        "--branch",
        help="Create a branch pointing at the imported root span.",
    ),
) -> None:
    """Import a trace from an OTel-compatible NDJSON file.

    Existing spans with the same id are left untouched (idempotent).
    A branch is optionally created so you can ``clew checkout`` into
    the imported trace. The format expected is whatever ``clew
    export`` produces, or a bare OTel NDJSON stream (one span dict
    per line, all sharing a ``trace_id``).
    """
    clew_path = _resolve_root(root)
    try:
        trace_id, spans = read_ndjson(ndjson)
    except (ValueError, OSError) as exc:
        _err(f"failed to read {ndjson}: {exc}")
    store, ts = _open_store(clew_path)
    added = 0
    for s in spans:
        try:
            ts.add_span(s)
            added += 1
        except Exception as exc:
            typer.echo(f"  warn: failed to import span {s.id}: {exc}", err=True)
    if branch_name and spans:
        from clew.core.branch import BranchManager

        BranchManager(ts).create(branch_name, spans[0].id)
    typer.echo(f"imported {added}/{len(spans)} spans, trace_id={trace_id}")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@app.command("query")
def cmd_query(
    name: str | None = typer.Option(
        None, "--name", help="Substring match (case-insensitive) on span name."
    ),
    type: str | None = typer.Option(
        None,
        "--type",
        help="Filter by span type (LLM, TOOL, DECISION, OBSERVATION).",
    ),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by span status (OK, ERROR).",
    ),
    trace_id: str | None = typer.Option(
        None, "--trace", help="Restrict to a single trace id."
    ),
    metadata: list[str] | None = typer.Option(
        None,
        "--metadata",
        help="Match metadata key=value (repeatable, all keys must match).",
    ),
    limit: int = typer.Option(
        50, "--limit", help="Maximum number of matches to return."
    ),
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Search spans across the store.

    All filters are AND-combined. Examples:

        clew query --name gpt-4o
        clew query --type LLM --status ERROR
        clew query --metadata model=gpt-4o --metadata temperature=0.7
    """
    clew_path = _resolve_root(root)
    try:
        type_enum = SpanType(type) if type else None
    except ValueError:
        _err(
            f"unknown type {type!r}; expected one of {[t.value for t in SpanType]}"
        )
    try:
        status_enum = SpanStatus(status) if status else None
    except ValueError:
        _err(
            f"unknown status {status!r}; expected one of {[s.value for s in SpanStatus]}"
        )
    try:
        meta = parse_metadata_spec(metadata or [])
    except ValueError as exc:
        _err(str(exc))
    filt = QueryFilter(
        name=name,
        type=type_enum,
        status=status_enum,
        trace_id=trace_id,
        metadata=meta or None,
        limit=limit,
    )
    results = query(clew_path, filt)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "count": len(results),
                    "matches": [
                        {
                            "span_id": r.span.id,
                            "trace_id": r.trace_id,
                            "root_span_id": r.root_span_id,
                            "type": r.span.type.value,
                            "name": r.span.name,
                            "status": r.span.status.value,
                            "started_at": r.span.started_at.isoformat(),
                            "ended_at": r.span.ended_at.isoformat(),
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
        return
    if not results:
        typer.echo("(no matches)")
        return
    from rich.table import Table

    table = Table(show_header=True, header_style="bold")
    table.add_column("span", style="cyan", no_wrap=True)
    table.add_column("type", style="magenta")
    table.add_column("status")
    table.add_column("name", style="bold")
    table.add_column("trace", style="dim")
    for r in results:
        status_style = "green" if r.span.status == SpanStatus.OK else "red"
        table.add_row(
            r.span.id[:12],
            r.span.type.value,
            f"[{status_style}]{r.span.status.value}[/{status_style}]",
            r.span.name,
            r.trace_id[:12],
        )
    Console().print(table)


# ---------------------------------------------------------------------------
# doctor / gc
# ---------------------------------------------------------------------------


@app.command("doctor")
def cmd_doctor(
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a panel."),
) -> None:
    """Check the store for corruption, missing refs, and orphan spans.

    Read-only: the doctor never modifies your store. It exits 0 if
    the store is healthy, 1 if any errors were found (warnings still
    pass).
    """
    clew_path = _resolve_root(root)
    r = check_store(clew_path)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "healthy": r.healthy,
                    "head": r.head,
                    "branches": list(r.branches),
                    "ref_count": r.ref_count,
                    "span_files": r.span_files,
                    "indexed_spans": r.indexed_spans,
                    "errors": [i.to_dict() for i in r.errors],
                    "warnings": [i.to_dict() for i in r.warnings],
                },
                indent=2,
            )
        )
    else:
        from rich.panel import Panel
        from rich.table import Table

        table = Table(show_header=True, header_style="bold")
        table.add_column("severity", style="bold")
        table.add_column("code")
        table.add_column("message")
        for i in r.errors:
            table.add_row(
                f"[red]{i.severity.value}[/red]", i.code, i.message
            )
        for i in r.warnings:
            table.add_row(
                f"[yellow]{i.severity.value}[/yellow]", i.code, i.message
            )
        if not r.issues:
            table.add_row("[green]ok[/green]", "-", "no issues found")
        summary = (
            f"head: {r.head}  branches: {len(r.branches)}  "
            f"spans: {r.span_files} files / {r.indexed_spans} indexed  "
            f"refs: {r.ref_count}"
        )
        Console().print(Panel(table, title="clew doctor", subtitle=summary))
    if not r.healthy:
        raise typer.Exit(code=1)


@app.command("gc")
def cmd_gc(
    root: Path = typer.Option(None, "--root", help="Path to the .clew directory."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report what would be deleted without actually removing anything.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Remove span files that are no longer reachable from any branch.

    A span is "orphan" iff no ref or ancestor chain leads to it. This
    happens naturally when you delete a branch. By default ``clew gc``
    is destructive; pass ``--dry-run`` to preview.
    """
    clew_path = _resolve_root(root)
    r = gc(clew_path, dry_run=dry_run)
    if as_json:
        typer.echo(json.dumps(r.to_dict(), indent=2))
    else:
        action = "would delete" if dry_run else "deleted"
        typer.echo(
            f"scanned {r.scanned} spans, {action} {r.deleted}, kept {r.kept}"
        )
        if r.deleted_ids and not as_json:
            sample = ", ".join(s[:12] for s in r.deleted_ids[:5])
            if len(r.deleted_ids) > 5:
                sample += f", … (+{len(r.deleted_ids) - 5} more)"
            typer.echo(f"  {sample}")


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


# ---------------------------------------------------------------------------
# mcp  (Model Context Protocol server)
# ---------------------------------------------------------------------------


@app.command("mcp")
def cmd_mcp() -> None:
    """Run the clew MCP server (stdio transport).

    Connect from any MCP-compatible client — Claude Desktop,
    Cursor, Cline, the MCP Inspector. See the README for
    one-line config snippets.
    """
    try:
        from clew.mcp_server import main as mcp_main
    except ImportError as exc:
        _err(
            f"MCP support requires the `mcp` package: {exc}. "
            "Install with `uv add 'clew[mcp]'`."
        )
    raise typer.Exit(code=mcp_main())


# ---------------------------------------------------------------------------
# bench — scaling benchmark
# ---------------------------------------------------------------------------


@app.command("bench")
def cmd_bench(
    spans: int = typer.Option(
        5_000, "--spans", help="Spans per trace for the scaling test."
    ),
    traces: int = typer.Option(
        100, "--traces", help="Number of traces to record."
    ),
    orphans: int = typer.Option(
        1_000, "--orphans", help="Number of orphan spans to GC."
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Optional JSON file to write the result to.",
    ),
) -> None:
    """Run the in-process scaling benchmark.

    Reports three timings:

    * ``record`` — record ``N`` traces of ``M`` spans each.
    * ``diff``   — diff the first and last recorded trace.
    * ``gc``     — run GC on ``K`` orphan spans.

    All operations run in a fresh tempdir; the existing store is
    untouched. Exits 0 on success; non-zero on a failed assertion.
    """
    import tempfile

    from clew.bench import bench as _run_bench

    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = _run_bench(
                Path(tmp) / ".clew",
                n_traces=traces,
                spans_per_trace=spans,
                n_orphans=orphans,
            )
        except AssertionError as exc:
            _err(f"bench failed: {exc}")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, indent=2), encoding="utf-8")
        typer.echo(f"wrote {out}")
    typer.echo(f"record  : {r['record_ms']:.0f}ms ({r['traces_recorded']} traces, "
               f"{r['spans_per_trace']} spans/trace)")
    typer.echo(f"diff    : {r['diff_ms']:.0f}ms ({r['diff_added']}+{r['diff_removed']}+{r['diff_changed']} changes)")
    typer.echo(f"gc      : {r['gc_ms']:.0f}ms ({r['orphans_scanned']} scanned, {r['orphans_deleted']} deleted)")
    typer.echo(f"dedup   : {r['dedup_unique']} unique ids from {r['dedup_inputs']} inputs")
