"""
Payout + IdempotencyKey models.

A `Payout` is the application-facing record of "merchant wants to move
N paise to bank account X". It carries the state machine. The actual
movement of money on the ledger is one or two `LedgerEntry` rows that
point at the payout.

An `IdempotencyKey` is the merchant-supplied UUID we use to dedupe API
requests. The key is scoped per-merchant — two different merchants can
use the same UUID without colliding. Keys live for 24 hours, after
which a fresh request with the same UUID is treated as new.
"""
from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from apps.merchants.models import BankAccount, Merchant

from .state_machine import PayoutStateConst


class PayoutState(models.TextChoices):
    PENDING = PayoutStateConst.PENDING, "Pending"
    PROCESSING = PayoutStateConst.PROCESSING, "Processing"
    COMPLETED = PayoutStateConst.COMPLETED, "Completed"
    FAILED = PayoutStateConst.FAILED, "Failed"


class FailureReason(models.TextChoices):
    BANK_REJECTED = "bank_rejected", "Bank rejected"
    EXHAUSTED_RETRIES = "exhausted_retries", "Exhausted retries"
    NONE = "", "None"


class Payout(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.PROTECT, related_name="payouts"
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="payouts"
    )
    amount_paise = models.BigIntegerField()
    state = models.CharField(
        max_length=20,
        choices=PayoutState.choices,
        default=PayoutState.PENDING,
    )
    # Number of times the worker has tried to settle this payout. We
    # increment this every time we flip pending → processing, and use it
    # to cap retries at PAYOUT_MAX_ATTEMPTS.
    attempts = models.IntegerField(default=0)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(
        max_length=40,
        choices=FailureReason.choices,
        default=FailureReason.NONE,
        blank=True,
    )
    failure_detail = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payouts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["merchant", "-created_at"]),
            # Worker scans this index every 10s for stuck payouts.
            models.Index(fields=["state", "last_attempted_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount_paise__gt=0),
                name="payouts_amount_positive",
            ),
            models.CheckConstraint(
                check=models.Q(attempts__gte=0),
                name="payouts_attempts_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"payout {self.id} {self.state} {self.amount_paise}p"

    @property
    def is_terminal(self) -> bool:
        return self.state in (PayoutState.COMPLETED, PayoutState.FAILED)


class IdempotencyKey(models.Model):
    """Cache row for the Idempotency-Key header.

    Lifecycle:
      * Created with status=IN_FLIGHT inside the same atomic block that
        creates the Payout. The unique constraint on
        (merchant_id, key) is what stops duplicate payouts.
      * Updated to status=COMPLETED with the cached response after the
        request finishes successfully.
      * If a second request with the same key arrives:
          - while status=IN_FLIGHT       → 409 Conflict
          - after status=COMPLETED       → return cached response
          - with a different request hash → 422 (key reuse with
            different body)
      * Rows older than 24h are eligible for cleanup; we treat them as
        absent on lookup.
    """

    class Status(models.TextChoices):
        IN_FLIGHT = "in_flight", "In flight"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="idempotency_keys"
    )
    key = models.CharField(max_length=80)
    # Hash of the canonicalized request body. If a second request has the
    # same key but a different body, the merchant has reused the key for
    # something else — that's a client bug and we surface it as 422.
    request_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.IN_FLIGHT
    )
    # Cached response, set once the first call completes. NULL while
    # in-flight.
    response_status_code = models.IntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    payout = models.ForeignKey(
        Payout,
        on_delete=models.SET_NULL,
        related_name="idempotency_keys",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "idempotency_keys"
        constraints = [
            # Per-merchant uniqueness. The unique constraint is what
            # actually enforces idempotency: a second concurrent INSERT
            # with the same (merchant, key) hits the unique violation
            # and we then read the existing row.
            models.UniqueConstraint(
                fields=["merchant", "key"],
                name="idempotency_keys_unique_per_merchant",
            ),
        ]
        indexes = [models.Index(fields=["created_at"])]

    def is_expired(self, ttl_seconds: int) -> bool:
        return (timezone.now() - self.created_at).total_seconds() > ttl_seconds
