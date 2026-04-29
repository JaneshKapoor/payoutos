"""
Business logic for payouts.

This module is the only place that mutates `Payout.state` or inserts a
`LedgerEntry`. Anything that needs to move money goes through here so
the locking and the state machine cannot be bypassed.

Two pieces matter most:

1. `request_payout` — handles the API call. Inside one DB transaction:
     a) SELECT FOR UPDATE the merchant row (Postgres row lock).
     b) Recompute available balance from the ledger.
     c) If the merchant can afford it, INSERT the payout and a matching
        DEBIT_PAYOUT_HOLD ledger entry.
     d) INSERT the IdempotencyKey row with the cached response.
   If two concurrent requests for the same merchant both arrive, only
   one of them holds the merchant row lock at a time, so the second
   one always observes the (possibly reduced) balance after the first
   committed. Postgres serializes them for us; we just have to ask.

2. `transition_to` — the only function that writes to `Payout.state`.
   It runs `assert_can_transition` *inside* the transaction so the
   state machine and the database write are atomic. A `failed`
   transition also inserts the reversal ledger entry in the same block.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ledger.models import LedgerEntry, LedgerEntryKind
from apps.ledger.services import get_balance
from apps.merchants.models import BankAccount, Merchant

from .models import (
    FailureReason,
    IdempotencyKey,
    Payout,
    PayoutState,
)
from .state_machine import assert_can_transition

logger = logging.getLogger(__name__)


# ---- Errors ---------------------------------------------------------------


class PayoutError(Exception):
    """Base class for client-visible payout errors."""

    code = "payout_error"
    http_status = 400


class InsufficientFunds(PayoutError):
    code = "insufficient_funds"
    http_status = 422


class IdempotencyKeyInFlight(PayoutError):
    """Same key, same body, but the original request hasn't finished yet."""

    code = "idempotency_key_in_flight"
    http_status = 409


class IdempotencyKeyConflict(PayoutError):
    """Same key, *different* body — the merchant reused a key."""

    code = "idempotency_key_conflict"
    http_status = 422


class InvalidBankAccount(PayoutError):
    code = "invalid_bank_account"
    http_status = 422


# ---- Idempotency helpers --------------------------------------------------


def _hash_request(body: dict[str, Any]) -> str:
    """Stable hash of the request payload, used to detect key reuse."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CachedResponse:
    status_code: int
    body: dict[str, Any]


def _lookup_idempotency(
    *, merchant_id: UUID, key: str, request_hash: str
) -> CachedResponse | None:
    """
    Returns a CachedResponse if the key has already been seen and is
    still within TTL.

    Raises IdempotencyKeyInFlight or IdempotencyKeyConflict if we hit
    the rough edges.
    """
    try:
        record = IdempotencyKey.objects.get(merchant_id=merchant_id, key=key)
    except IdempotencyKey.DoesNotExist:
        return None

    ttl = settings.IDEMPOTENCY_TTL_HOURS * 3600
    if record.is_expired(ttl):
        # Expired keys are treated as if they didn't exist. We delete
        # the row so the new INSERT below can take its place.
        record.delete()
        return None

    if record.request_hash != request_hash:
        # Same key, different body. Client bug.
        raise IdempotencyKeyConflict(
            "idempotency key reused with a different request body"
        )

    if record.status == IdempotencyKey.Status.IN_FLIGHT:
        # First request still running. Tell the client to retry. Don't
        # block — we don't want N requests holding connections.
        raise IdempotencyKeyInFlight("original request still processing")

    return CachedResponse(
        status_code=record.response_status_code,
        body=record.response_body,
    )


# ---- Payout creation ------------------------------------------------------


@dataclass
class PayoutCreateResult:
    payout: Payout
    cached: bool


def request_payout(
    *,
    merchant_id: UUID,
    bank_account_id: UUID,
    amount_paise: int,
    idempotency_key: str,
    request_body: dict[str, Any],
) -> PayoutCreateResult:
    """Create a payout, holding funds, in a way that's safe under
    concurrency and idempotent under retries.

    Caller is the API view. View is responsible for translating
    `PayoutError` subclasses to HTTP responses.
    """
    if amount_paise <= 0:
        raise PayoutError("amount_paise must be positive")
    if not idempotency_key:
        raise PayoutError("Idempotency-Key header is required")

    request_hash = _hash_request(request_body)

    # ---- Fast path: if we've seen this key before, short-circuit.
    # Done outside the main transaction so we don't hold a write lock
    # while answering a duplicate.
    cached = _lookup_idempotency(
        merchant_id=merchant_id, key=idempotency_key, request_hash=request_hash
    )
    if cached is not None:
        # Refetch the payout to give the client the freshest state in
        # the response. The cached body's IDs are still valid.
        payout_id = cached.body.get("payout", {}).get("id")
        payout = Payout.objects.get(id=payout_id) if payout_id else None
        return PayoutCreateResult(payout=payout, cached=True)

    # ---- Slow path: actually create.
    # Everything below runs in a single transaction. If anything raises,
    # neither the payout, the ledger entry, nor the idempotency record
    # is committed — we cannot end up in a half-written state.
    try:
        with transaction.atomic():
            # 1. Lock the merchant row. This is the linchpin of the
            #    concurrency story: two simultaneous requests for the
            #    same merchant will both try to take this lock; one
            #    waits while the other commits. By the time the second
            #    one runs the balance check below, it sees the debit
            #    inserted by the first.
            try:
                merchant = (
                    Merchant.objects.select_for_update().get(id=merchant_id)
                )
            except Merchant.DoesNotExist:
                raise PayoutError("merchant not found")

            # 2. Validate bank account belongs to merchant.
            try:
                bank_account = BankAccount.objects.get(
                    id=bank_account_id, merchant_id=merchant_id
                )
            except BankAccount.DoesNotExist:
                raise InvalidBankAccount(
                    "bank account does not belong to this merchant"
                )

            # 3. Recompute balance from the ledger *while holding the
            #    merchant lock*. Cannot be stale relative to other
            #    concurrent payout requests.
            balance = get_balance(merchant.id)
            if balance.available_paise < amount_paise:
                raise InsufficientFunds(
                    f"available {balance.available_paise}p, "
                    f"requested {amount_paise}p"
                )

            # 4. Create the payout row.
            payout = Payout.objects.create(
                merchant=merchant,
                bank_account=bank_account,
                amount_paise=amount_paise,
                state=PayoutState.PENDING,
            )

            # 5. Insert the matching debit ledger entry. Negative paise
            #    because debit. The check constraint on the table
            #    enforces the sign.
            LedgerEntry.objects.create(
                merchant=merchant,
                kind=LedgerEntryKind.DEBIT_PAYOUT_HOLD,
                amount_paise=-amount_paise,
                payout=payout,
                description=f"Hold for payout {payout.id}",
            )

            # 6. Persist the idempotency record. If two requests with
            #    the same key raced past the fast-path lookup, the
            #    unique constraint will fire here and we'll hit the
            #    IntegrityError handler below.
            response_body = _build_payout_response(payout)
            IdempotencyKey.objects.create(
                merchant=merchant,
                key=idempotency_key,
                request_hash=request_hash,
                status=IdempotencyKey.Status.COMPLETED,
                response_status_code=201,
                response_body=response_body,
                payout=payout,
            )

    except IntegrityError:
        # Another concurrent request beat us to inserting the
        # idempotency row. Re-read it; it's the source of truth now.
        cached = _lookup_idempotency(
            merchant_id=merchant_id,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if cached is None:
            # Extremely unlikely — would mean the row vanished between
            # our IntegrityError and this lookup. Surface it as a 5xx.
            raise
        payout_id = cached.body.get("payout", {}).get("id")
        payout = Payout.objects.get(id=payout_id) if payout_id else None
        return PayoutCreateResult(payout=payout, cached=True)

    # 7. Hand off to the worker. Outside the transaction so the row is
    #    visible to the worker before it picks the task up.
    from .tasks import process_payout  # local import to dodge circular
    process_payout.delay(str(payout.id))

    return PayoutCreateResult(payout=payout, cached=False)


def _build_payout_response(payout: Payout) -> dict[str, Any]:
    return {
        "payout": {
            "id": str(payout.id),
            "merchant_id": str(payout.merchant_id),
            "bank_account_id": str(payout.bank_account_id),
            "amount_paise": payout.amount_paise,
            "state": payout.state,
            "attempts": payout.attempts,
            "created_at": payout.created_at.isoformat(),
        }
    }


# ---- State transitions ----------------------------------------------------


def transition_to(
    *,
    payout_id: UUID,
    target_state: str,
    failure_reason: str = FailureReason.NONE,
    failure_detail: str = "",
) -> Payout:
    """The only function that flips Payout.state.

    Runs assert_can_transition inside a transaction so the legality
    check and the UPDATE are one atomic operation. If the target is
    FAILED, also inserts the credit_payout_reversal entry that returns
    the held funds — atomically, in the same block.
    """
    with transaction.atomic():
        # Lock the payout row. Two workers could pick the same payout
        # off the queue (we deliberately don't trust the queue to
        # dedupe); the lock means only one of them performs the
        # transition.
        payout = Payout.objects.select_for_update().get(id=payout_id)

        assert_can_transition(payout.state, target_state)

        if target_state == PayoutState.PROCESSING:
            payout.state = PayoutState.PROCESSING
            payout.attempts += 1
            payout.last_attempted_at = timezone.now()
        elif target_state == PayoutState.COMPLETED:
            payout.state = PayoutState.COMPLETED
            payout.completed_at = timezone.now()
        elif target_state == PayoutState.FAILED:
            payout.state = PayoutState.FAILED
            payout.failure_reason = failure_reason or FailureReason.BANK_REJECTED
            payout.failure_detail = failure_detail or ""
            payout.completed_at = timezone.now()
            # Return the held funds. Atomic with the state flip.
            LedgerEntry.objects.create(
                merchant_id=payout.merchant_id,
                kind=LedgerEntryKind.CREDIT_PAYOUT_REVERSAL,
                amount_paise=payout.amount_paise,
                payout=payout,
                description=f"Reversal for failed payout {payout.id}",
            )
        else:  # pragma: no cover — state machine should have rejected it
            raise AssertionError(f"unhandled target state {target_state}")

        payout.save()
        logger.info(
            "payout %s transitioned to %s (attempts=%d)",
            payout.id,
            payout.state,
            payout.attempts,
        )
        return payout
