"""
Concurrency test from the challenge:

  > A merchant with 100 rupees balance submits two simultaneous
  > 60 rupee payout requests. Exactly one should succeed. The other
  > must be rejected cleanly.

We spawn two threads, each calling `request_payout` with a unique
idempotency key (i.e. these are two genuine, distinct requests, not
the same request retried). Both threads use the same merchant. We
assert:

  * exactly one returns a Payout in PENDING state
  * exactly one raises InsufficientFunds
  * the merchant's available_paise is exactly 40_00 (100 - 60) at the
    end (one debit applied, the other rejected)

Why this is the test that matters: a naive "balance = compute(); if
balance >= amount: insert(amount)" implementation passes the
single-threaded happy path and *also* passes if the two reads happen
to interleave benignly. The only way to catch it deterministically is
to actually run two threads and assert the postcondition. That's what
this test does.

The test runs against SQLite in `manage.py test` by default, but the
locking story is only meaningful against Postgres — so we skip when
the engine isn't postgres. Run via:

    DATABASE_URL=postgres://... python manage.py test apps.payouts.tests
"""
from __future__ import annotations

import threading
import uuid
from unittest.mock import patch

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.ledger.models import LedgerEntry, LedgerEntryKind
from apps.ledger.services import get_balance
from apps.merchants.models import BankAccount, Merchant
from apps.payouts.models import PayoutState
from apps.payouts.services import (
    InsufficientFunds,
    PayoutCreateResult,
    PayoutError,
    request_payout,
)


# Use TransactionTestCase, not TestCase, because we need real commits —
# Django's TestCase wraps each test in a transaction that rolls back at
# the end, which prevents the SELECT FOR UPDATE in a second thread from
# observing the first thread's writes.
class ConcurrentPayoutsTest(TransactionTestCase):
    reset_sequences = True

    def run(self, *args, **kwargs):
        # Stub the celery dispatch so request_payout does not require a
        # live broker. We're testing the API/locking layer here, not the
        # worker — the worker has its own tests.
        with patch("apps.payouts.tasks.process_payout.delay") as m:
            m.return_value = None
            return super().run(*args, **kwargs)

    def setUp(self) -> None:
        # Skip if we're not on postgres. Locking semantics on SQLite
        # don't model what we're testing.
        engine = connections["default"].vendor
        if engine != "postgresql":
            self.skipTest(
                f"concurrency test requires postgres (got {engine!r})"
            )

        self.merchant = Merchant.objects.create(
            id=uuid.uuid4(),
            name="Concurrency Test Co",
            email=f"conc-{uuid.uuid4()}@test.local",
        )
        self.bank = BankAccount.objects.create(
            merchant=self.merchant,
            account_holder_name="Concurrency Test Co",
            account_number="0000000001",
            ifsc="TEST0001234",
            is_primary=True,
        )
        # Seed 100 rupees = 10_000 paise.
        LedgerEntry.objects.create(
            merchant=self.merchant,
            kind=LedgerEntryKind.CREDIT_CUSTOMER_PAYMENT,
            amount_paise=10_000,
            description="seed",
        )

    def _request(self, amount_paise: int, results: list, idx: int) -> None:
        try:
            result = request_payout(
                merchant_id=self.merchant.id,
                bank_account_id=self.bank.id,
                amount_paise=amount_paise,
                idempotency_key=f"concurrency-test-{idx}-{uuid.uuid4()}",
                request_body={"amount_paise": amount_paise},
            )
            results[idx] = ("ok", result)
        except PayoutError as e:
            results[idx] = ("err", e)
        finally:
            # Each thread holds its own DB connection. We MUST close it
            # before the test finishes, otherwise Django's test runner
            # cannot drop the test database (it has open sessions).
            connections.close_all()

    def test_two_simultaneous_60_rupee_payouts_only_one_succeeds(self) -> None:
        results: list = [None, None]
        t1 = threading.Thread(target=self._request, args=(6_000, results, 0))
        t2 = threading.Thread(target=self._request, args=(6_000, results, 1))
        t1.start(); t2.start()
        t1.join(); t2.join()

        kinds = [r[0] for r in results]
        self.assertEqual(
            sorted(kinds),
            ["err", "ok"],
            f"expected exactly one ok and one err, got {kinds}",
        )

        ok_idx = kinds.index("ok")
        err_idx = kinds.index("err")

        ok_result: PayoutCreateResult = results[ok_idx][1]
        err: PayoutError = results[err_idx][1]

        self.assertIsInstance(err, InsufficientFunds)
        self.assertEqual(ok_result.payout.state, PayoutState.PENDING)
        self.assertEqual(ok_result.payout.amount_paise, 6_000)

        # Postcondition: 100 - 60 = 40 rupees available, 60 held.
        snap = get_balance(self.merchant.id)
        self.assertEqual(snap.available_paise, 4_000)
        self.assertEqual(snap.held_paise, 6_000)

    def test_a_third_request_after_both_completes_sees_correct_balance(self) -> None:
        """Sanity: the surviving request created exactly one debit row."""
        results: list = [None, None]
        t1 = threading.Thread(target=self._request, args=(6_000, results, 0))
        t2 = threading.Thread(target=self._request, args=(6_000, results, 1))
        t1.start(); t2.start()
        t1.join(); t2.join()

        debit_count = LedgerEntry.objects.filter(
            merchant=self.merchant,
            kind=LedgerEntryKind.DEBIT_PAYOUT_HOLD,
        ).count()
        self.assertEqual(debit_count, 1)
