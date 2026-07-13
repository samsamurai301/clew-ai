"""Entry point for ``python -m clew``.

The CLI is owned by the ``cli`` task; this file is the spec-required
two-liner that wires ``python -m clew`` to the Typer app.
"""

from __future__ import annotations

from clew.cli import app

app()
