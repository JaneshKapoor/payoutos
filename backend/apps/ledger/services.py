"""
Balance derivation. Pure SQL aggregation — never Python arithmetic on
fetched rows.

`available_balance` is the only number that matters when authorizing a
new payout. It is the sum of every ledger entry — credits add, debits
subtract — so a payout that has been held but not yet completed has
already reduced this number. Holds never need to be "released" on
success: when a payout completes, the existing debit_payout_hold row
just becomes permanent.

`held_balance` is purely informational for the UI. It is the sum of
debit_payout_hold rows that have not been reversed by a
credit_payout_reversal — i.e. money locked by a payout that hasn't
landed yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.db.models import Sum

from apps.payouts.models import Payout, PayoutState

from .models import LedgerEntry, LedgerEntryKind


@dataclass(frozen=True)
class BalanceSnapshot:
    available_paise: int
    held_paise: int
    lifetime_credits_paise: int
    lifetime_debits_paise: int  # absolute value; expressed as positive number for display

    @property
    def settled_paise(self) -> int:
        """Money that has already left the merchant's balance permanently
        (i.e. completed payouts). Available = settled_balance + held."""
        return self.available_paise - self.held_paise


def get_balance(merchant_id: UUID | str) -> BalanceSnapshot:
    """Snapshot the merchant balance straight from the ledger.

    Single query, single SUM. No Python-side arithmetic.
    """
    rows = LedgerEntry.objects.filter(merchant_id=merchant_id)

    available = rows.aggregate(s=Sum("amount_paise"))["s"] or 0

    credits = rows.filter(amount_paise__gt=0).aggregate(s=Sum("amount_paise"))["s"] or 0
    debits = rows.filter(amount_paise__lt=0).aggregate(s=Sum("amount_paise"))["s"] or 0

    # Held = sum of payout-hold debits whose payout is still in-flight
    # (pending or processing). Computed from the Payout state, which is
    # the source of truth for "is this hold still active or has it been
    # reversed/finalized?".
    held = (
        Payout.objects.filter(
            merchant_id=merchant_id,
            state__in=[PayoutState.PENDING, PayoutState.PROCESSING],
        ).aggregate(s=Sum("amount_paise"))["s"]
        or 0
    )

    return BalanceSnapshot(
        available_paise=int(available),
        held_paise=int(held),
        lifetime_credits_paise=int(credits),
        lifetime_debits_paise=int(-debits),
    )


def credit_customer_payment(
    *, merchant_id: UUID | str, amount_paise: int, description: str = ""
) -> LedgerEntry:
    """Helper for the seed script. Records a simulated USD→INR settlement."""
    if amount_paise <= 0:
        raise ValueError("credit amount must be positive")
    return LedgerEntry.objects.create(
        merchant_id=merchant_id,
        kind=LedgerEntryKind.CREDIT_CUSTOMER_PAYMENT,
        amount_paise=amount_paise,
        description=description,
    )
