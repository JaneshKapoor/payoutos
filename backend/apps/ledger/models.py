"""
Append-only event-sourced ledger.

Every change to a merchant's balance is a row in `ledger_entries`. We never
update an existing row. Balance is always derived in SQL:

    balance = SUM(amount_paise) WHERE merchant_id = X

Credits store positive amounts. Debits store negative amounts. The signing
is enforced at the application layer (see `LedgerEntryKind.sign`) and
defended in tests, but the storage is plain signed integers so the
SUM aggregation is trivially correct under any concurrency.

Why event-sourced and not a `merchants.balance` column?
  * The balance column would have to be updated under a lock for every
    movement. The ledger row is a fresh INSERT — append-only, no UPDATE.
  * Auditability: every paisa is justified by a row that points to the
    payout, customer payment, or reversal that caused it.
  * The "credits minus debits = balance" invariant the challenge calls
    out is automatically true, because *that is the definition* of
    balance in this design.

The locking story (see payouts.services) is layered on top: we
SELECT FOR UPDATE the merchant row before reading the running balance
and inserting a new debit, so two concurrent payouts can't both observe
sufficient funds and both insert their debits.
"""
import uuid

from django.db import models

from apps.merchants.models import Merchant


class LedgerEntryKind(models.TextChoices):
    """Why money moved.

    Sign convention:
      CREDIT_*  → +amount    (money coming into the merchant balance)
      DEBIT_*   → -amount    (money leaving the merchant balance)
    """

    CREDIT_CUSTOMER_PAYMENT = "credit_customer_payment", "Customer payment"
    DEBIT_PAYOUT_HOLD = "debit_payout_hold", "Payout hold"
    CREDIT_PAYOUT_REVERSAL = "credit_payout_reversal", "Payout reversal"

    @classmethod
    def sign(cls, kind: "LedgerEntryKind | str") -> int:
        """+1 for credits, -1 for debits."""
        return 1 if str(kind).startswith("credit_") else -1


class LedgerEntry(models.Model):
    """Immutable. Never updated. Inserted exactly once per money movement."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    kind = models.CharField(max_length=40, choices=LedgerEntryKind.choices)
    # Signed paise. Credits positive, debits negative.
    # BigIntegerField, never Decimal/Float — we deal in the smallest currency
    # unit so all arithmetic is integer arithmetic.
    amount_paise = models.BigIntegerField()
    # Optional pointer to the payout this entry belongs to.
    # We use a string FK target to avoid an import cycle with payouts.
    payout = models.ForeignKey(
        "payouts.Payout",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )
    # Free-form description for the audit trail / dashboard.
    description = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_entries"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["merchant", "-created_at"]),
            models.Index(fields=["payout"]),
        ]
        constraints = [
            # Never allow a zero entry. Zero entries pollute the audit trail.
            models.CheckConstraint(
                check=~models.Q(amount_paise=0),
                name="ledger_entry_amount_not_zero",
            ),
            # Sign matches the kind. Credits must be positive, debits must
            # be negative. Caught at write time, not just by code.
            models.CheckConstraint(
                check=(
                    models.Q(kind__startswith="credit_", amount_paise__gt=0)
                    | models.Q(kind__startswith="debit_", amount_paise__lt=0)
                ),
                name="ledger_entry_sign_matches_kind",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount_paise:+d}p ({self.merchant_id})"
