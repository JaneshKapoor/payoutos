import uuid

from django.db import models


class Merchant(models.Model):
    """A merchant who collects USD and gets paid out in INR.

    The merchant row itself is the lock anchor: when we modify a merchant's
    balance we SELECT FOR UPDATE on this row, so concurrent payouts for the
    same merchant serialize, while payouts for different merchants can run
    in parallel.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "merchants"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.name


class BankAccount(models.Model):
    """A merchant's payout destination."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="bank_accounts"
    )
    account_holder_name = models.CharField(max_length=200)
    account_number = models.CharField(max_length=32)
    ifsc = models.CharField(max_length=16)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_accounts"
        constraints = [
            models.UniqueConstraint(
                fields=["merchant", "account_number", "ifsc"],
                name="bank_accounts_unique_per_merchant",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account_holder_name} ({self.account_number[-4:]})"
