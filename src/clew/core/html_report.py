"""Render a trace as a self-contained HTML page.

The output is a single file with all CSS + JS inlined. No
external dependencies, no CDN, no analytics. Drop it on disk
and email it; it works offline.

The page renders the trace as a tree (collapsible per branch),
highlights ERROR spans in red, shows the input/output of each
span on demand, and includes a summary panel with the trace's
stats (span count, total duration, error count, max depth).
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from clew.core.models import Span, SpanStatus, Trace

_CSS = """
:root {
  --bg: #0d1117;
  --bg-2: #161b22;
  --bg-3: #1f2530;
  --fg: #c9d1d9;
  --fg-bright: #f0f6fc;
  --muted: #8b949e;
  --border: #30363d;
  --accent: #58a6ff;
  --accent-2: #79c0ff;
  --ok: #3fb950;
  --error: #f85149;
  --warning: #d29922;
  --purple: #bc8cff;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
               Roboto, "Helvetica Neue", sans-serif;
  background: var(--bg);
  color: var(--fg);
  margin: 0; padding: 0; line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent-2); text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }

/* ----- Header ----- */
header {
  display: flex; align-items: baseline; gap: 16px;
  padding-bottom: 16px; border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
header h1 {
  font-size: 1.6em; color: var(--fg-bright);
  margin: 0; font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
header .pill {
  display: inline-block; padding: 2px 10px;
  border: 1px solid var(--border); border-radius: 999px;
  font-size: 0.8em; color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.meta { color: var(--muted); font-size: 0.9em; margin: 8px 0 0; }
.meta code { background: var(--bg-2); padding: 2px 6px; border-radius: 3px; }

/* ----- Stats panel ----- */
.stats {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px; margin: 24px 0;
}
.stat {
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
}
.stat .label {
  font-size: 0.75em; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.06em;
  font-weight: 600;
}
.stat .value {
  font-size: 1.6em; color: var(--fg-bright);
  font-weight: 600; margin-top: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.stat.error .value { color: var(--error); }
.stat.ok .value { color: var(--ok); }
.stat.accent .value { color: var(--accent); }

/* ----- Controls ----- */
.controls {
  margin: 16px 0; display: flex; gap: 8px; align-items: center;
}
.controls button {
  background: var(--bg-2); color: var(--fg);
  border: 1px solid var(--border); padding: 5px 12px;
  border-radius: 6px; cursor: pointer;
  font-size: 0.85em; font-family: inherit;
}
.controls button:hover { border-color: var(--accent); color: var(--fg-bright); }
.controls .search {
  background: var(--bg-2); color: var(--fg);
  border: 1px solid var(--border); padding: 5px 10px;
  border-radius: 6px; flex: 1; max-width: 360px;
  font-size: 0.85em; font-family: inherit;
}
.controls .search:focus { outline: none; border-color: var(--accent); }

/* ----- Tree ----- */
ul.tree, ul.tree ul {
  list-style: none; padding-left: 20px; margin: 0;
}
ul.tree { padding-left: 0; }
.span {
  border: 1px solid var(--border);
  border-radius: 6px;
  margin: 4px 0;
  background: var(--bg-2);
  transition: border-color 0.1s;
}
.span.error { border-color: var(--error); }
.span.running { border-color: var(--warning); }
.span-head {
  display: flex; gap: 8px; align-items: center;
  padding: 8px 12px; cursor: pointer; user-select: none;
}
.span-head:hover { background: var(--bg-3); }
.caret {
  color: var(--muted); width: 1em; flex-shrink: 0;
  font-family: ui-monospace, monospace; font-size: 0.8em;
}
.name {
  font-weight: 600; color: var(--fg-bright);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.type {
  color: var(--accent-2); font-size: 0.78em;
  background: var(--bg);
  padding: 1px 6px; border-radius: 3px;
  font-family: ui-monospace, monospace;
  border: 1px solid var(--border);
}
.status-ok { color: var(--ok); font-size: 0.78em; }
.status-error { color: var(--error); font-size: 0.78em; font-weight: 600; }
.status-running { color: var(--warning); font-size: 0.78em; }
.id { color: var(--muted); font-size: 0.72em;
      font-family: ui-monospace, monospace; margin-left: auto; }
.duration { color: var(--muted); font-size: 0.78em; }
.details {
  margin: 0 12px 10px 28px; padding: 10px 12px;
  background: var(--bg); border-radius: 4px;
  border-left: 2px solid var(--border);
  display: none;
}
.details.open { display: block; }
.details .label {
  color: var(--muted); font-size: 0.7em;
  text-transform: uppercase; letter-spacing: 0.06em;
  font-weight: 600; margin: 8px 0 2px;
}
.details pre {
  overflow-x: auto; white-space: pre-wrap; word-wrap: break-word;
  max-width: 100%; font-size: 0.82em; margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--fg);
  line-height: 1.5;
}

/* ----- Footer ----- */
footer {
  margin-top: 40px; padding-top: 16px;
  border-top: 1px solid var(--border);
  color: var(--muted); font-size: 0.85em;
  display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px;
}
footer a { color: var(--accent-2); }
"""


_JS = """
document.querySelectorAll('.span-head').forEach(head => {
  head.addEventListener('click', () => {
    const d = head.nextElementSibling;
    if (d && d.classList.contains('details')) {
      d.classList.toggle('open');
    }
    const caret = head.querySelector('.caret');
    if (caret) {
      caret.textContent = d && d.classList.contains('open') ? '▼' : '▶';
    }
  });
});
document.querySelectorAll('[data-action="expand-all"]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.details').forEach(e => e.classList.add('open'));
    document.querySelectorAll('.caret').forEach(c => c.textContent = '▼');
  });
});
document.querySelectorAll('[data-action="collapse-all"]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.details').forEach(e => e.classList.remove('open'));
    document.querySelectorAll('.caret').forEach(c => c.textContent = '▶');
  });
});
const search = document.querySelector('[data-role="search"]');
if (search) {
  search.addEventListener('input', () => {
    const q = search.value.toLowerCase();
    document.querySelectorAll('.span').forEach(s => {
      const text = s.textContent.toLowerCase();
      s.style.display = (!q || text.includes(q)) ? '' : 'none';
    });
  });
}
"""


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>clew trace {trace_id}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
  <header>
    <h1>clew trace <span class="id">{trace_id}</span></h1>
    <span class="pill">{span_count} span{plural} · {error_count} error{error_plural}</span>
  </header>
  <p class="meta">
    root: <code>{root_id}</code> · depth: <strong>{max_depth}</strong> ·
    duration: <strong>{total_duration_ms:.0f}ms</strong> · started: {started_at}
  </p>

  <div class="stats">
    <div class="stat">
      <div class="label">Spans</div>
      <div class="value">{span_count}</div>
    </div>
    <div class="stat {error_class}">
      <div class="label">Errors</div>
      <div class="value">{error_count}</div>
    </div>
    <div class="stat accent">
      <div class="label">Max depth</div>
      <div class="value">{max_depth}</div>
    </div>
    <div class="stat">
      <div class="label">Total time</div>
      <div class="value">{total_duration_ms:.0f}ms</div>
    </div>
  </div>

  <div class="controls">
    <input type="search" class="search" data-role="search" placeholder="Filter spans by name, type, content…" />
    <button data-action="expand-all">Expand all</button>
    <button data-action="collapse-all">Collapse all</button>
  </div>

  <ul class="tree">
{html_tree}
  </ul>

  <footer>
    <span>Generated by <a href="https://github.com/clew/clew">clew</a></span>
    <span class="id">{generated_at}</span>
  </footer>
</div>
<script>{js}</script>
</body>
</html>
"""


def _format_value(value: object) -> str:
    """Best-effort string repr for input/output/metadata values."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _format_duration_ms(s: Span) -> float:
    """Return the span's duration in milliseconds (0 if not set)."""
    if not s.started_at or not s.ended_at:
        return 0.0
    return max(0.0, (s.ended_at - s.started_at).total_seconds() * 1000)


def _status_class(s: Span) -> str:
    """Return the CSS class for the span's status."""
    if s.status is SpanStatus.ERROR:
        return "status-error"
    if s.status is SpanStatus.RUNNING:
        return "status-running"
    return "status-ok"


def _render_span(s: Span) -> str:
    """Render a single span as HTML."""
    duration_ms = _format_duration_ms(s)
    err_class = (
        " error"
        if s.status is SpanStatus.ERROR
        else (" running" if s.status is SpanStatus.RUNNING else "")
    )
    status_class = _status_class(s)
    caret = "▶"
    parts: list[str] = []
    parts.append(f'<li><div class="span{err_class}">')
    parts.append(
        f'<div class="span-head"><span class="caret">{caret}</span>'
        f'<span class="name">{html.escape(s.name)}</span>'
        f'<span class="type">{html.escape(s.type.value)}</span>'
        f'<span class="{status_class}">{html.escape(s.status.value)}</span>'
        f'<span class="duration">{duration_ms:.1f}ms</span>'
        f'<span class="id">{html.escape(s.id[:12])}</span></div>'
    )
    parts.append('<div class="details">')
    parts.append(
        f'<div class="label">id</div><pre>{html.escape(s.id)}</pre>'
    )
    if s.parent_ids:
        parts.append(
            f'<div class="label">parent_ids</div>'
            f'<pre>{html.escape(", ".join(s.parent_ids))}</pre>'
        )
    if s.error:
        parts.append(
            f'<div class="label">error</div>'
            f'<pre>{html.escape(s.error)}</pre>'
        )
    if s.attributes:
        parts.append(
            f'<div class="label">attributes</div>'
            f'<pre>{html.escape(_format_value(s.attributes))}</pre>'
        )
    if s.input is not None:
        parts.append(
            f'<div class="label">input</div>'
            f'<pre>{html.escape(_format_value(s.input))}</pre>'
        )
    if s.output is not None:
        parts.append(
            f'<div class="label">output</div>'
            f'<pre>{html.escape(_format_value(s.output))}</pre>'
        )
    if s.metadata:
        parts.append(
            f'<div class="label">metadata</div>'
            f'<pre>{html.escape(_format_value(s.metadata))}</pre>'
        )
    parts.append("</div></div>")
    return "\n".join(parts)


def _compute_max_depth(trace: Trace) -> int:
    """Return the deepest nesting level in the trace (1-indexed)."""
    by_parent: dict[str, list[Span]] = defaultdict(list)
    for s in trace.spans:
        for p in s.parent_ids:
            by_parent[p].append(s)
    roots = [s for s in trace.spans if not s.parent_ids]

    def _depth(s: Span) -> int:
        kids = by_parent.get(s.id, [])
        if not kids:
            return 1
        return 1 + max(_depth(c) for c in kids)

    if not roots:
        return 0
    return max(_depth(r) for r in roots)


def render_html(trace: Trace) -> str:
    """Render a :class:`Trace` as a self-contained HTML page.

    The output uses a collapsible tree: every span is a clickable
    card that reveals its input / output / metadata on demand.
    ERROR spans get a red border so they stand out. A stats panel
    at the top shows span count, error count, max depth, and
    total duration. A search box filters spans in place.
    """
    # Build a parent -> children map.
    children: dict[str, list[Span]] = defaultdict(list)
    by_id = {s.id: s for s in trace.spans}
    for s in trace.spans:
        for parent in s.parent_ids:
            children[parent].append(s)
    # Order children by started_at for stability.
    for k in children:
        children[k].sort(key=lambda s: s.started_at or _MIN)

    def _render_subtree(span_id: str) -> str:
        s = by_id[span_id]
        kids = children.get(span_id, [])
        parts = [_render_span(s)]
        if kids:
            parts.append("<ul>")
            for c in kids:
                parts.append(_render_subtree(c.id))
            parts.append("</ul>")
        return "\n".join(parts)

    root = next((s for s in trace.spans if s.id == trace.root_span_id), None)
    if root is None and trace.spans:
        root = trace.spans[0]
    if root is None:
        html_tree = "<li><em>(empty trace)</em></li>"
    else:
        html_tree = _render_subtree(root.id)

    error_count = sum(1 for s in trace.spans if s.status is SpanStatus.ERROR)
    total_duration = sum(_format_duration_ms(s) for s in trace.spans)
    started = root.started_at.isoformat() if root and root.started_at else "?"
    # ``html.escape`` blocks XSS but doesn't strip ``{`` / ``}``.
    # Because we use ``str.format`` to fill the template, an
    # attacker-controlled ``{name}`` in a value would either be
    # re-substituted (if ``name`` is a kwarg) or raise (if it
    # isn't). Belt-and-braces: replace ``{`` and ``}`` with their
    # HTML-entity equivalents before substitution.
    def _esc(s: str) -> str:
        return html.escape(s).replace("{", "&#123;").replace("}", "&#125;")

    return _TEMPLATE.format(
        css=_CSS,
        js=_JS,
        trace_id=_esc(trace.trace_id[:16] or trace.trace_id),
        root_id=_esc(trace.root_span_id[:16] or trace.root_span_id),
        span_count=len(trace.spans),
        plural="s" if len(trace.spans) != 1 else "",
        error_count=error_count,
        error_plural="s" if error_count != 1 else "",
        error_class="error" if error_count else "ok",
        max_depth=_compute_max_depth(trace),
        total_duration_ms=total_duration,
        started_at=_esc(started),
        html_tree=html_tree,
        generated_at=_esc(_now()),
    )


def write_html(trace: Trace, out: Path) -> Path:
    """Render the trace to ``out`` and return ``out``.

    The file is self-contained: drop it in an email, an S3 bucket,
    or a GitHub gist; the recipient can open it in any browser.
    """
    out.write_text(render_html(trace), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_MIN = datetime.min.replace(tzinfo=UTC)


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = ["render_html", "write_html"]
