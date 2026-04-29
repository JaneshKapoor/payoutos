"""
Celery workers for the payout pipeline.

Two tasks:
  * `process_payout(payout_id)` — picks up a single payout, flips it to
    PROCESSING, simulates a bank settlement, and either completes or
    fails it. Retries on the random "hang" outcome.
  * `scan_stuck_payouts()` — periodic janitor that picks up payouts
    that have been in PROCESSING (or PENDING) longer than the stuck
    threshold and re-fires `process_payout`.

Why a periodic janitor instead of "just retry inside the task"?
  * Workers can crash. A retry callback inside a worker that has just
    been kill -9'd doesn't fire. The janitor runs from beat, in a
    different process — it's the only thing that recovers from an
    actual worker death.
  * Idempotency: the periodic scanner is the same code path the API
    uses to enqueue, so there's only one way to "wake up" a payout.

Retry policy:
  * Max attempts: settings.PAYOUT_MAX_ATTEMPTS (3).
  * Backoff: exponential by attempt count (2s, 4s, 8s) — encoded as a
    `last_attempted_at + backoff < now` check the scanner runs.
  * After max attempts, the payout transitions to FAILED, which atomically
    inserts the reversal entry that returns the held funds.
"""
from __future__ import annotations

import logging
import random
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import FailureReason, Payout, PayoutState
from .services import transition_to
from .state_machine import IllegalStateTransition

logger = logging.getLogger(__name__)


# ---- Bank simulation ------------------------------------------------------


def _simulate_bank_settlement() -> str:
    """70% success, 20% fail, 10% hang.

    "Hang" returns "hang" — the task will exit without flipping state, and
    the periodic scanner will pick the payout up after the stuck timeout
    and retry.
    """
    roll = random.random()
    if roll < 0.70:
        return "success"
    if roll < 0.90:
        return "fail"
    return "hang"


# ---- Single-payout processor ---------------------------------------------


@shared_task(name="apps.payouts.tasks.process_payout", bind=True)
def process_payout(self, payout_id: str) -> str:
    """Move a single payout through pending → processing → completed/failed.

    Returns the terminal state for observability/tests.
    """
    # Step 1: flip pending → processing under a row lock. If the payout
    # is already processing/terminal (because another worker raced us),
    # the state machine raises and we exit cleanly.
    try:
        payout = transition_to(
            payout_id=payout_id, target_state=PayoutState.PROCESSING
        )
    except IllegalStateTransition:
        logger.info(
            "process_payout: payout %s no longer pending; skipping",
            payout_id,
        )
        return "skipped"

    # Step 2: simulate the bank.
    outcome = _simulate_bank_settlement()
    logger.info("process_payout %s attempt=%d outcome=%s", payout_id,
                payout.attempts, outcome)

    if outcome == "success":
        transition_to(payout_id=payout_id, target_state=PayoutState.COMPLETED)
        return "completed"

    if outcome == "fail":
        # Hard fail: don't burn retries on a bank rejection. We could
        # be smarter (NSF retryable, account-closed not), but for the
        # simulation any "fail" is terminal.
        if payout.attempts >= settings.PAYOUT_MAX_ATTEMPTS:
            transition_to(
                payout_id=payout_id,
                target_state=PayoutState.FAILED,
                failure_reason=FailureReason.EXHAUSTED_RETRIES,
                failure_detail=f"bank rejected after {payout.attempts} attempts",
            )
        else:
            transition_to(
                payout_id=payout_id,
                target_state=PayoutState.FAILED,
                failure_reason=FailureReason.BANK_REJECTED,
                failure_detail="simulated bank rejection",
            )
        return "failed"

    # outcome == "hang": leave it in PROCESSING; scan_stuck_payouts will
    # retry it after the stuck timeout. We deliberately do NOT reschedule
    # ourselves here — beat is the only retry trigger.
    logger.warning(
        "process_payout %s simulated hang; will be retried by scanner",
        payout_id,
    )
    return "hung"


# ---- Periodic stuck-payout scanner ---------------------------------------


def _backoff_seconds(attempts: int) -> int:
    """Exponential backoff: 2s, 4s, 8s for attempts 1, 2, 3."""
    return 2 ** max(attempts, 1)


@shared_task(name="apps.payouts.tasks.scan_stuck_payouts")
def scan_stuck_payouts() -> dict[str, int]:
    """Find payouts the worker abandoned and either retry or fail them.

    Two cases we handle:
      A. PENDING payouts the API enqueued but the worker never picked
         up (rare — process died after the API committed but before
         celery delivered). Re-enqueue.
      B. PROCESSING payouts whose last attempt is older than the stuck
         threshold AND the exponential-backoff window for the next
         attempt. Either retry (if under attempt cap) or fail with
         EXHAUSTED_RETRIES (if at the cap).
    """
    now = timezone.now()
    stuck_cutoff = now - timedelta(seconds=settings.PAYOUT_STUCK_AFTER_SECONDS)

    requeued = 0
    failed = 0
    retried = 0

    # Case A: ghost pending payouts.
    ghosts = list(
        Payout.objects.filter(
            state=PayoutState.PENDING,
            created_at__lt=stuck_cutoff,
        ).values_list("id", flat=True)
    )
    for pid in ghosts:
        process_payout.delay(str(pid))
        requeued += 1

    # Case B: stuck PROCESSING payouts.
    candidates = list(
        Payout.objects.filter(
            state=PayoutState.PROCESSING,
            last_attempted_at__lt=stuck_cutoff,
        ).values_list("id", "attempts", "last_attempted_at")
    )

    for pid, attempts, last_attempted in candidates:
        # Exponential backoff: 2s, 4s, 8s on top of the 30s stuck window.
        # Skip if we're still inside the backoff for this payout.
        backoff_floor = stuck_cutoff - timedelta(seconds=_backoff_seconds(attempts))
        if last_attempted and last_attempted > backoff_floor:
            continue

        outcome = _retry_stuck_payout(pid)
        if outcome == "completed":
            pass
        elif outcome == "failed":
            failed += 1
        elif outcome == "retried":
            retried += 1

    logger.info(
        "scan_stuck_payouts: requeued=%d retried=%d failed=%d",
        requeued, retried, failed,
    )
    return {"requeued": requeued, "retried": retried, "failed": failed}


def _retry_stuck_payout(payout_id) -> str:
    """One stuck-payout's retry attempt. Returns the outcome label."""
    with transaction.atomic():
        try:
            p = Payout.objects.select_for_update(skip_locked=True).get(id=payout_id)
        except Payout.DoesNotExist:
            return "missing"

        # Re-check inside the lock — another scanner may have already
        # finalized this row.
        if p.state != PayoutState.PROCESSING:
            return "skipped"

        # If we've already used all our attempts, this stuck pickup is
        # the death sentence. Fail + refund inside the lock.
        if p.attempts >= settings.PAYOUT_MAX_ATTEMPTS:
            transition_to(
                payout_id=p.id,
                target_state=PayoutState.FAILED,
                failure_reason=FailureReason.EXHAUSTED_RETRIES,
                failure_detail=(
                    f"stuck in processing after {p.attempts} attempts"
                ),
            )
            return "failed"

        # Otherwise, count this as a fresh attempt and re-simulate.
        # We bump `attempts` directly here because PROCESSING→PROCESSING
        # is not a real state transition — only the per-attempt
        # bookkeeping changes.
        Payout.objects.filter(id=p.id, state=PayoutState.PROCESSING).update(
            attempts=F("attempts") + 1,
            last_attempted_at=timezone.now(),
        )
        p.refresh_from_db()

    # Bank simulation runs OUTSIDE the lock — locks shouldn't span
    # network calls (here, simulated). Once we know the outcome, take
    # the lock again to apply the terminal transition.
    outcome = _simulate_bank_settlement()
    logger.info(
        "scan_stuck_payouts retry %s attempts=%d outcome=%s",
        p.id, p.attempts, outcome,
    )

    if outcome == "success":
        transition_to(payout_id=p.id, target_state=PayoutState.COMPLETED)
        return "completed"
    if outcome == "fail":
        transition_to(
            payout_id=p.id,
            target_state=PayoutState.FAILED,
            failure_reason=FailureReason.BANK_REJECTED,
            failure_detail="bank rejected on retry",
        )
        return "failed"
    # outcome == "hang": leave PROCESSING; next scan will pick it up
    # again after the larger backoff window.
    return "retried"


# ---- Idempotency cleanup --------------------------------------------------


@shared_task(name="apps.payouts.tasks.purge_expired_idempotency_keys")
def purge_expired_idempotency_keys() -> int:
    """Delete idempotency rows older than the TTL. Called from beat or
    cron; not on the hot path."""
    from .models import IdempotencyKey

    cutoff = timezone.now() - timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS)
    deleted, _ = IdempotencyKey.objects.filter(created_at__lt=cutoff).delete()
    return deleted
