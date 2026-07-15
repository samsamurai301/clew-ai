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
* Span and trace ids are independent 32-character UUID hex strings.
* ``content_hash`` is a 64-character SHA-256 integrity digest computed only
  after the span is final.
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

#: Length of the hexadecimal representation of a UUID.
UUID_HEX_LEN: Final[int] = 32

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
    """The terminal state of a persisted :class:`Span`.

    In-flight state is deliberately internal to the tracer and is never
    represented by a public or persisted ``Span``.
    """

    OK = "OK"
    """The span completed successfully."""

    ERROR = "ERROR"
    """The span raised or returned an error; ``Span.error`` carries
    the human-readable message."""

    SKIPPED = "SKIPPED"
    """The span was not executed because a dependency failed."""


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
    """A single finalized reasoning step.

    ``id`` identifies this occurrence and is independent of its content.
    ``content_hash`` protects the exact persisted record. This separation
    means two identical calls remain two independently addressable events.

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
        min_length=UUID_HEX_LEN,
        max_length=UUID_HEX_LEN,
        pattern=r"^[0-9a-f]{32}$",
        description=(
            "Unique lowercase UUID4 hex occurrence identity. It is not derived from span content."
        ),
    )
    trace_id: str = Field(
        ...,
        min_length=UUID_HEX_LEN,
        max_length=UUID_HEX_LEN,
        pattern=r"^[0-9a-f]{32}$",
        description=(
            "Independent lowercase UUID4 hex identity shared by every span in this execution trace."
        ),
    )
    parent_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered list of direct parent span ids. Empty for the "
            "root span of a trace; multiple for join/merge spans."
        ),
    )
    sequence: int = Field(
        default=0,
        ge=0,
        description="Unique, monotonically increasing execution order in the trace.",
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
        description=("UTC wall-clock end time, timezone-aware. Always >= started_at."),
    )
    status: SpanStatus = Field(..., description="OK, ERROR, or SKIPPED.")

    error: str | None = Field(
        default=None,
        description=(
            "Human-readable error message. Only populated when status == ERROR. None otherwise."
        ),
    )

    # ---- provenance ------------------------------------------------------

    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional free-form provenance (SDK version, host, model "
            "id, etc.). Included in the integrity hash."
        ),
    )
    content_hash: str = Field(
        default="",
        description=(
            "SHA-256 of canonical JSON for every persisted field except "
            "content_hash itself. Computed after finalization."
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
        if self.status is not SpanStatus.ERROR and self.error:
            raise ValueError(
                "Span.error is populated but status is not ERROR; "
                "only ERROR spans may carry an error message."
            )
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("Span.parent_ids contains a duplicate parent id.")
        for parent_id in self.parent_ids:
            if len(parent_id) != UUID_HEX_LEN or any(
                char not in "0123456789abcdef" for char in parent_id
            ):
                raise ValueError(
                    f"Span.parent_ids contains malformed id {parent_id!r}; "
                    "expected 32 lowercase hexadecimal characters."
                )
        if self.id in self.parent_ids:
            raise ValueError("Span cannot list itself as a parent.")
        if self.ended_at < self.started_at:
            raise ValueError(
                "Span.ended_at is before Span.started_at; "
                f"got started_at={self.started_at!r} "
                f"ended_at={self.ended_at!r}."
            )
        # Import lazily to avoid a module cycle: hash utilities depend on Span.
        from clew.utils.hash import span_hash

        expected_hash = span_hash(self)
        if not self.content_hash:
            object.__setattr__(self, "content_hash", expected_hash)
        elif self.content_hash != expected_hash:
            raise ValueError(
                "Span.content_hash does not match the finalized record: "
                f"expected {expected_hash}, got {self.content_hash}."
            )


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class Trace(BaseModel):
    """A DAG projection over a set of spans sharing a ``trace_id``.

    A ``Trace`` is a *convenience* object: the source of truth is the
    set of immutable JSON span records under ``.clew/spans/``. The
    TUI and SDK frequently want the whole tree in one object, so we
    provide it here.

    The :class:`Trace` model is built by the storage layer from a
    trace_id; it is not written to disk directly.
    """

    model_config = _FROZEN_STRICT

    trace_id: str = Field(
        ...,
        description="Independent 32-character UUID hex trace identity.",
    )
    root_span_id: str = Field(
        ...,
        description=("Span id of the trace's entry span (no parents within this trace)."),
    )
    spans: list[Span] = Field(
        default_factory=list,
        description=(
            "All spans in deterministic sequence order. Parents must appear before their children."
        ),
    )

    def model_post_init(self, __context: object) -> None:
        """Validate identity, ordering, and complete DAG topology."""
        ids = {span.id for span in self.spans}
        if len(ids) != len(self.spans):
            raise ValueError("Trace.spans contains duplicate span ids.")
        if self.root_span_id not in ids:
            raise ValueError(
                f"Trace.root_span_id={self.root_span_id!r} is not "
                "present in Trace.spans; every trace must contain its "
                "root span."
            )
        foreign = [span.id for span in self.spans if span.trace_id != self.trace_id]
        if foreign:
            raise ValueError(f"Trace contains spans with a different trace_id: {foreign}.")
        sequences = [span.sequence for span in self.spans]
        if len(sequences) != len(set(sequences)):
            raise ValueError("Trace contains duplicate sequence values.")
        roots = [span.id for span in self.spans if not span.parent_ids]
        if roots != [self.root_span_id]:
            raise ValueError(
                f"Trace must have exactly root_span_id={self.root_span_id!r}; found roots {roots}."
            )
        for span in self.spans:
            missing = [parent for parent in span.parent_ids if parent not in ids]
            if missing:
                raise ValueError(f"Span {span.id} has missing parents {missing}.")
        in_degree = {span.id: len(span.parent_ids) for span in self.spans}
        children: dict[str, list[str]] = {span.id: [] for span in self.spans}
        for span in self.spans:
            for parent_id in span.parent_ids:
                children[parent_id].append(span.id)
        ready = [span_id for span_id, degree in in_degree.items() if degree == 0]
        resolved_count = 0
        while ready:
            span_id = ready.pop()
            resolved_count += 1
            for child_id in children[span_id]:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    ready.append(child_id)
        if resolved_count != len(self.spans):
            raise ValueError("Trace topology contains a cycle.")


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
        min_length=UUID_HEX_LEN,
        max_length=UUID_HEX_LEN,
        pattern=r"^[0-9a-f]{32}$",
        description=("Span occurrence id at the tip of this branch."),
    )
    created_at: datetime = Field(
        ...,
        description=("UTC timestamp at which this branch was created. Timezone-aware."),
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
        min_length=UUID_HEX_LEN,
        max_length=UUID_HEX_LEN,
        pattern=r"^[0-9a-f]{32}$",
        description="32-character UUID hex span occurrence id this ref resolves to.",
    )
    updated_at: datetime = Field(
        ...,
        description=("UTC timestamp of the most recent write to this ref. Timezone-aware."),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "SHA256_HEX_LEN",
    "UUID_HEX_LEN",
    "Branch",
    "Ref",
    "Span",
    "SpanStatus",
    "SpanType",
    "Trace",
]
