"""
State machine guard tests. Quick to run, doubles as living docs of
what's legal and what isn't.
"""
from __future__ import annotations

from django.test import TestCase

from apps.payouts.models import PayoutState
from apps.payouts.state_machine import (
    IllegalStateTransition,
    assert_can_transition,
    is_terminal,
)


class StateMachineTest(TestCase):
    def test_pending_can_become_processing_or_failed(self) -> None:
        assert_can_transition(PayoutState.PENDING, PayoutState.PROCESSING)
        assert_can_transition(PayoutState.PENDING, PayoutState.FAILED)

    def test_processing_can_become_completed_or_failed(self) -> None:
        assert_can_transition(PayoutState.PROCESSING, PayoutState.COMPLETED)
        assert_can_transition(PayoutState.PROCESSING, PayoutState.FAILED)

    def test_completed_is_terminal(self) -> None:
        self.assertTrue(is_terminal(PayoutState.COMPLETED))
        for target in (
            PayoutState.PENDING,
            PayoutState.PROCESSING,
            PayoutState.FAILED,
        ):
            with self.assertRaises(IllegalStateTransition):
                assert_can_transition(PayoutState.COMPLETED, target)

    def test_failed_is_terminal(self) -> None:
        self.assertTrue(is_terminal(PayoutState.FAILED))
        for target in (
            PayoutState.PENDING,
            PayoutState.PROCESSING,
            PayoutState.COMPLETED,
        ):
            with self.assertRaises(IllegalStateTransition):
                assert_can_transition(PayoutState.FAILED, target)

    def test_no_backwards_moves(self) -> None:
        with self.assertRaises(IllegalStateTransition):
            assert_can_transition(PayoutState.PROCESSING, PayoutState.PENDING)
