"""Document-processing state machine.

The lifecycle is explicit and enforced. The important guarantees:

  * A document is only ``completed`` when every page finished real processing.
    Partial work can never reach ``completed`` — it goes to ``review_required``
    (if human review is needed) or ``failed``.
  * ``failed`` records a bounded error *category*, never raw exception text.
  * ``completed`` is terminal for processing; the only further move is
    ``deletion_pending``.
  * Retrying a ``failed`` document goes back through ``queued``; it does not
    jump straight to ``completed``.
"""

from __future__ import annotations

from typing import Optional

from ...errors import ConflictError
from ...models.documents import DocumentProcessingState as S

# Allowed transitions. Anything not listed here is rejected.
_ALLOWED: dict[S, frozenset[S]] = {
    S.uploaded: frozenset({S.queued, S.cancelled, S.deletion_pending}),
    S.queued: frozenset({S.preprocessing, S.failed, S.cancelled, S.deletion_pending}),
    S.preprocessing: frozenset(
        {S.ocr_processing, S.failed, S.cancelled, S.deletion_pending}
    ),
    S.ocr_processing: frozenset(
        {S.review_required, S.completed, S.failed, S.cancelled, S.deletion_pending}
    ),
    S.review_required: frozenset(
        {S.completed, S.failed, S.cancelled, S.deletion_pending}
    ),
    # Terminal for processing.
    S.completed: frozenset({S.deletion_pending}),
    # Recovery paths.
    S.failed: frozenset({S.queued, S.cancelled, S.deletion_pending}),
    S.cancelled: frozenset({S.queued, S.deletion_pending}),
    # Deletion, once started, only completes.
    S.deletion_pending: frozenset({S.deleted}),
    S.deleted: frozenset(),
}

# States that require every page to have been processed before entering.
_REQUIRES_ALL_PAGES = frozenset({S.completed})

# States that mean the pipeline is done for this document (no further work).
TERMINAL_STATES = frozenset({S.deleted})

# States that mean no further automatic processing will happen on their own.
IDLE_STATES = frozenset({S.completed, S.failed, S.cancelled, S.review_required})


def can_transition(current: S, target: S) -> bool:
    return target in _ALLOWED.get(current, frozenset())


def allowed_targets(current: S) -> frozenset[S]:
    return _ALLOWED.get(current, frozenset())


def assert_transition(current: S, target: S) -> None:
    """Raise ``ConflictError`` if the move is not on the allowed graph."""
    if not can_transition(current, target):
        raise ConflictError(
            f"Illegal document state transition {current.value} -> {target.value}",
            code="illegal_state_transition",
            details={"from": current.value, "to": target.value},
        )


def parse_state(value: Optional[str]) -> S:
    """Parse a stored state string, or raise a clear error if unknown."""
    try:
        return S(value)
    except (ValueError, TypeError):
        raise ConflictError(
            f"Unknown document processing state '{value}'", code="unknown_state"
        ) from None


def mark_completed(
    current: S,
    *,
    total_pages: int,
    processed_pages: int,
) -> S:
    """Decide the next state after a processing pass.

    Returns ``completed`` only if every page actually processed; otherwise
    ``review_required``. This is the single choke point that enforces the rule
    "partial completion never becomes completed".
    """
    assert_transition(current, S.completed)
    if total_pages > 0 and processed_pages < total_pages:
        assert_transition(current, S.review_required)
        return S.review_required
    return S.completed
