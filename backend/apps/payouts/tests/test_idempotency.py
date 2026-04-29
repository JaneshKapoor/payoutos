"""
Idempotency tests.

The challenge spec for idempotency:

  > The Idempotency-Key header is a merchant-supplied UUID. Second
  > call with the same key returns the exact same response as the
  > first. No duplicate payout created. Keys scoped per merchant.
  > Keys expire after 24 hours.

We test the three behaviors that matter:

  1. Same key, same body, same merchant → second call returns the
     same payout, no second debit ledger entry, no new payout row.
  2. Same key, *different* body → returns 422 IdempotencyKeyConflict.
     This is the case where a buggy client reused a key for two
     different operations.
  3. Same key, different merchant → both succeed (keys are scoped).
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase

from apps.ledger.models import LedgerEntry, LedgerEntryKind
from apps.merchants.models import BankAccount, Merchant
from apps.payouts.models import IdempotencyKey, Payout
from apps.payouts.services import (
    IdempotencyKeyConflict,
    request_payout,
)


def _seed_merchant(name: str, credit_paise: int) -> tuple[Merchant, BankAccount]:
    merchant = Merchant.objects.create(
        id=uuid.uuid4(), name=name, email=f"{uuid.uuid4()}@test.local"
    )
    bank = BankAccount.objects.create(
        merchant=merchant,
        account_holder_name=name,
        account_number=str(uuid.uuid4().int)[:12],
        ifsc="TEST0001234",
        is_primary=True,
    )
    LedgerEntry.objects.create(
        merchant=merchant,
        kind=LedgerEntryKind.CREDIT_CUSTOMER_PAYMENT,
        amount_paise=credit_paise,
        description="seed",
    )
    return merchant, bank


class IdempotencyTest(TestCase):
    def run(self, *args, **kwargs):
        # Don't dispatch real Celery tasks — we're testing the API layer.
        with patch("apps.payouts.tasks.process_payout.delay") as m:
            m.return_value = None
            return super().run(*args, **kwargs)

    def test_same_key_returns_same_payout_without_creating_second(self) -> None:
        merchant, bank = _seed_merchant("Idem One", 100_00)
        key = "client-supplied-uuid-001"
        body = {
            "amount_paise": 30_00,
            "bank_account_id": str(bank.id),
        }

        first = request_payout(
            merchant_id=merchant.id,
            bank_account_id=bank.id,
            amount_paise=30_00,
            idempotency_key=key,
            request_body=body,
        )
        second = request_payout(
            merchant_id=merchant.id,
            bank_account_id=bank.id,
            amount_paise=30_00,
            idempotency_key=key,
            request_body=body,
        )

        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(first.payout.id, second.payout.id)

        # Exactly one payout, exactly one debit, exactly one idempotency
        # key row.
        self.assertEqual(Payout.objects.filter(merchant=merchant).count(), 1)
        self.assertEqual(
            LedgerEntry.objects.filter(
                merchant=merchant,
                kind=LedgerEntryKind.DEBIT_PAYOUT_HOLD,
            ).count(),
            1,
        )
        self.assertEqual(
            IdempotencyKey.objects.filter(merchant=merchant, key=key).count(),
            1,
        )

    def test_same_key_different_body_raises_conflict(self) -> None:
        merchant, bank = _seed_merchant("Idem Two", 100_00)
        key = "client-supplied-uuid-002"

        request_payout(
            merchant_id=merchant.id,
            bank_account_id=bank.id,
            amount_paise=30_00,
            idempotency_key=key,
            request_body={
                "amount_paise": 30_00,
                "bank_account_id": str(bank.id),
            },
        )
        with self.assertRaises(IdempotencyKeyConflict):
            request_payout(
                merchant_id=merchant.id,
                bank_account_id=bank.id,
                amount_paise=50_00,  # different body
                idempotency_key=key,
                request_body={
                    "amount_paise": 50_00,
                    "bank_account_id": str(bank.id),
                },
            )

    def test_keys_scoped_per_merchant(self) -> None:
        m1, b1 = _seed_merchant("Idem A", 100_00)
        m2, b2 = _seed_merchant("Idem B", 100_00)
        shared_key = "same-key-different-merchants"

        r1 = request_payout(
            merchant_id=m1.id,
            bank_account_id=b1.id,
            amount_paise=10_00,
            idempotency_key=shared_key,
            request_body={"amount_paise": 10_00},
        )
        r2 = request_payout(
            merchant_id=m2.id,
            bank_account_id=b2.id,
            amount_paise=10_00,
            idempotency_key=shared_key,
            request_body={"amount_paise": 10_00},
        )

        self.assertFalse(r1.cached)
        self.assertFalse(r2.cached)
        self.assertNotEqual(r1.payout.id, r2.payout.id)
        self.assertEqual(Payout.objects.count(), 2)
