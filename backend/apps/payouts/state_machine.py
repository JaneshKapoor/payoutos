"""
Payout state machine.

Legal transitions:
    PENDING    → PROCESSING
    PROCESSING → COMPLETED
    PROCESSING → FAILED
    PENDING    → FAILED   (we let the worker fail a pending payout if it
                           exhausts attempts before ever picking it up)

Anything else — including all backwards moves like FAILED → COMPLETED
or COMPLETED → PENDING — raises IllegalStateTransition.

Why is this a class instead of "just check it inline":
  * The check is centralized so we cannot accidentally bypass it from a
    new code path.
  * The set of legal moves is small enough to fit on screen, so a
    reviewer can audit it at a glance.
  * `assert_can_transition` is what we call inside the DB transaction
    that mutates state — see `payouts.services.transition_to`.
"""
from __future__ import annotations

from typing import Iterable


class PayoutStateConst:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# Source-of-truth adjacency map. Key = current state. Value = set of states
# the payout is allowed to move into next.
_LEGAL: dict[str, frozenset[str]] = {
    PayoutStateConst.PENDING: frozenset(
        {PayoutStateConst.PROCESSING, PayoutStateConst.FAILED}
    ),
    PayoutStateConst.PROCESSING: frozenset(
        {PayoutStateConst.COMPLETED, PayoutStateConst.FAILED}
    ),
    PayoutStateConst.COMPLETED: frozenset(),  # terminal
    PayoutStateConst.FAILED: frozenset(),  # terminal
}


class IllegalStateTransition(Exception):
    """Raised when code tries to move a payout into an illegal next state."""

    def __init__(self, current: str, attempted: str):
        super().__init__(
            f"illegal payout state transition: {current!r} -> {attempted!r}"
        )
        self.current = current
        self.attempted = attempted


def is_terminal(state: str) -> bool:
    return len(_LEGAL[state]) == 0


def legal_next_states(state: str) -> Iterable[str]:
    return _LEGAL[state]


def assert_can_transition(current: str, target: str) -> None:
    """The single chokepoint that every state change must pass through.

    Any code that wants to mutate `Payout.state` calls this first. If it
    raises, the caller MUST NOT save the new state.
    """
    if target not in _LEGAL.get(current, frozenset()):
        raise IllegalStateTransition(current, target)
