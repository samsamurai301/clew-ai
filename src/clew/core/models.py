"""Canonical Pydantic v2 data models for clew.

These four models — :class:`Span`, :class:`Trace`, :class:`Branch`,
:class:`Ref` — are the **only** domain types the rest of the codebase
imports. They are intentionally small, frozen, and ``extra="forbid"``
so that hashing is deterministic and bundle I/O is round-trippable.

See :file:`PROTOCOL.md` for the byte-level serialization format
referenced throughout.

Conventions
-----------
* All fields are required unless explicitly marked ``Optional``.
* All models are frozen (immutable after construction).
* Unknown fields on construction raise ``ValidationError``; the
  storage layer is responsible for tolerating unknown fields on read.
* Datetimes are timezone-aware UTC (``datetime`` instances with
  ``tzinfo=timezone.utc``). Pydantic serializes them as RFC 3339
  strings with the trailing ``Z`` for UTC.
* SHA-256 hex ids are 64-character lowercase hexadecimal strings.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Length of a SHA-256 hex digest (lowercase).
SHA256_HEX_LEN: Final[int] = 64

#: Pattern-matching helper for SHA-256 hex strings. Not used as a strict
#: validator on the model (we trust the store to produce correct ids),
#: but exposed for callers that want to assert on id shape.
_HEX_64: Final[str] = r"^[0-9a-f]{64}$"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SpanType(StrEnum):
    """The kind of reasoning step a :class:`Span` represents.

    Maps 1:1 to the OTel ``gen_ai.*`` operation categories where they
    exist; the names are kept short and self-explanatory.
    """

    LLM = "LLM"
    """A chat-completion call (OpenAI, Anthropic, etc.)."""

    TOOL = "TOOL"
    """A tool/function invocation. Inputs are the call arguments,
    outputs are the tool result."""

    DECISION = "DECISION"
    """An explicit branching point — the agent chose branch A over B.
    Required to be branchable in the clew TUI."""

    OBSERVATION = "OBSERVATION"
    """An external event worth recording (a user message, a retrieval
    hit, a system note) that is not itself an LLM or tool call."""


class SpanStatus(StrEnum):
    """The terminal state of a :class:`Span`.

    ``RUNNING`` is only valid for in-flight spans that have not yet
    been written to disk. The store rewrites ``RUNNING`` spans as
    ``OK`` or ``ERROR`` when the operation completes (a separate
    append, since spans are append-only).
    """

    OK = "OK"
    """The span completed successfully."""

    ERROR = "ERROR"
    """The span raised or returned an error; ``Span.error`` carries
    the human-readable message."""

    RUNNING = "RUNNING"
    """The span is in flight. Not persisted in this state by the
    canonical writer; see :class:`Span` docstring for the rewrite
    protocol."""


# ---------------------------------------------------------------------------
# Shared model config
# ---------------------------------------------------------------------------

#: Strict, frozen, no-extras config used by every model in this module.
#: ``frozen=True`` makes the model hashable and immutable; ``extra="forbid"``
#: makes unknown fields a ``ValidationError`` at construction time.
_FROZEN_STRICT: Final[ConfigDict] = ConfigDict(
    frozen=True,
    extra="forbid",
    populate_by_name=True,
    str_strip_whitespace=False,
    validate_assignment=False,
)


# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------


class Span(BaseModel):
    """A single content-addressed reasoning step.

    A span is the atomic unit of clew. Every span has a deterministic
    SHA-256 ``id`` derived from the canonical-JSON serialization of
    all *other* fields. The id is the path on disk; the content is
    the bytes; they match by construction.

    Spans are append-only: once written, they are never modified. To
    "edit" a span, create a new span with the new content and update
    the relevant branch ref.

    Required fields are exactly those listed in :file:`PROTOCOL.md` §2;
    nothing is optional beyond the two explicitly optional fields
    (``error`` and ``metadata``).
    """

    model_config = _FROZEN_STRICT

    # ---- identity --------------------------------------------------------

    id: str = Field(
        ...,
        description=(
            "Lowercase hex (8-64 chars) of the canonical-JSON "
            "serialization of this span with the `id` field removed. "
            "Computed by `clew.utils.hash.content_hash`; the storage "
            "layer is responsible for setting it before write. The "
            "store layer also enforces the format — any non-hex "
            "id is rejected before it can reach the filesystem."
        ),
    )
    trace_id: str = Field(
        ...,
        description=(
            "Lowercase hex of the root span of the trace this span "
            "belongs to. Constant for every span in a trace."
        ),
    )
    parent_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered list of direct parent span ids. Empty for the "
            "root span of a trace; multiple for join/merge spans."
        ),
    )

    # ---- kind ------------------------------------------------------------

    type: SpanType = Field(..., description="The kind of reasoning step.")
    name: str = Field(
        ...,
        max_length=200,
        description="Human-readable label, e.g. 'plan', 'search_web'.",
    )

    # ---- payload ---------------------------------------------------------

    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form OTel-style attributes. Keys should use the "
            "OpenTelemetry dot namespace (e.g. 'gen_ai.system'). "
            "Never null; use an empty dict when there are no attributes."
        ),
    )
    input: Any = Field(
        default=None,
        description=(
            "Request payload, opaque to clew. May be null for spans "
            "where input is not meaningful (e.g. OBSERVATION)."
        ),
    )
    output: Any = Field(
        default=None,
        description=(
            "Response payload, opaque to clew. May be null for spans "
            "where output is not meaningful."
        ),
    )

    # ---- timing & status -------------------------------------------------

    started_at: datetime = Field(
        ...,
        description=(
            "UTC wall-clock start time, timezone-aware. Serialized as "
            "RFC 3339 with trailing 'Z' for UTC by Pydantic."
        ),
    )
    ended_at: datetime = Field(
        ...,
        description=(
            "UTC wall-clock end time, timezone-aware. Always >= "
            "started_at; for a RUNNING span, equals started_at."
        ),
    )
    status: SpanStatus = Field(..., description="OK, ERROR, or RUNNING.")

    error: str | None = Field(
        default=None,
        description=(
            "Human-readable error message. Only populated when "
            "status == ERROR. None otherwise."
        ),
    )

    # ---- provenance ------------------------------------------------------

    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional free-form provenance (SDK version, host, model "
            "id, etc.). Not content-addressed — included in the hash "
            "but not required to be meaningful across runs."
        ),
    )

    # ---- validation ------------------------------------------------------

    def model_post_init(self, __context: object) -> None:
        """Cross-field invariants.

        Cheap to check and catches whole classes of user error
        before the span is hashed.
        """
        if self.status is SpanStatus.ERROR and not self.error:
            # An ERROR span must carry a non-empty message.
            raise ValueError(
                "Span.status is ERROR but Span.error is empty; "
                "an ERROR span must carry a non-empty error message."
            )
        if self.ended_at < self.started_at:
            raise ValueError(
                "Span.ended_at is before Span.started_at; "
                f"got started_at={self.started_at!r} "
                f"ended_at={self.ended_at!r}."
            )


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class Trace(BaseModel):
    """A Merkle-DAG projection over a set of spans sharing a ``trace_id``.

    A ``Trace`` is a *convenience* object: the source of truth is the
    set of span files in ``.clew/objects/span/<id>`` and the
    append-only JSONL log at ``.clew/traces/<trace_id>.jsonl``. The
    TUI and SDK frequently want the whole tree in one object, so we
    provide it here.

    The :class:`Trace` model is built by the storage layer from a
    trace_id; it is not written to disk directly.
    """

    model_config = _FROZEN_STRICT

    trace_id: str = Field(
        ...,
        description="SHA-256 hex of the root span of the trace.",
    )
    root_span_id: str = Field(
        ...,
        description=(
            "Span id of the trace's entry span (no parents within "
            "this trace)."
        ),
    )
    spans: list[Span] = Field(
        default_factory=list,
        description=(
            "All spans in the trace, in insertion order (the order "
            "they were appended to the JSONL log). Does not have to "
            "be topologically sorted."
        ),
    )

    def model_post_init(self, __context: object) -> None:
        """Sanity-check that root_span_id is reachable from the spans."""
        ids = {span.id for span in self.spans}
        if self.root_span_id not in ids:
            raise ValueError(
                f"Trace.root_span_id={self.root_span_id!r} is not "
                "present in Trace.spans; every trace must contain its "
                "root span."
            )


# ---------------------------------------------------------------------------
# Branch
# ---------------------------------------------------------------------------


class Branch(BaseModel):
    """A named, mutable pointer to a span — the unit of user-visible state.

    A branch is what ``clew branch foo`` creates and ``clew checkout``
    moves. Internally, branches are just ``Ref``s in
    ``.clew/refs/heads/<name>``; the :class:`Branch` wrapper exists
    for ergonomic SDK access and to record provenance (when the
    branch was created — ``Refs`` only track last-update time).
    """

    model_config = _FROZEN_STRICT

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description=(
            "Branch name. Allowed characters: ASCII letters, digits, "
            "'-', '_', '/', '.'. Reserved: 'HEAD', '@'."
        ),
    )
    head_span_id: str = Field(
        ...,
        description=(
            "Span id at the tip of this branch. Must be a 64-char "
            "lowercase hex SHA-256 string."
        ),
    )
    created_at: datetime = Field(
        ...,
        description=(
            "UTC timestamp at which this branch was created. "
            "Timezone-aware."
        ),
    )


# ---------------------------------------------------------------------------
# Ref
# ---------------------------------------------------------------------------


class Ref(BaseModel):
    """A generic named pointer to a span.

    Refs are the on-disk storage primitive: a ref is a single-line
    text file at ``.clew/refs/<category>/<name>`` whose contents are
    a span id. Categories include ``heads/<branch>``, ``tags/<tag>``,
    and ``remotes/<remote>/<branch>``.

    Unlike :class:`Branch`, a :class:`Ref` does not record when it
    was *created* — only when it was last *updated*. (A branch's
    creation time is preserved on the :class:`Branch` object built
    on top of the underlying :class:`Ref`.)
    """

    model_config = _FROZEN_STRICT

    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description=(
            "Full ref name including the category, e.g. "
            "'refs/heads/main', 'refs/tags/v0.1.0', 'HEAD'."
        ),
    )
    span_id: str = Field(
        ...,
        description="Span id this ref resolves to (64-char hex SHA-256).",
    )
    updated_at: datetime = Field(
        ...,
        description=(
            "UTC timestamp of the most recent write to this ref. "
            "Timezone-aware."
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "SHA256_HEX_LEN",
    "Branch",
    "Ref",
    "Span",
    "SpanStatus",
    "SpanType",
    "Trace",
]
