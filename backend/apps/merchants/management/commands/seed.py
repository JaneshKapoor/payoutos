"""
Seed merchants with deterministic IDs so the demo URL is stable.

Run with: python manage.py seed [--reset]
"""
from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.ledger.models import LedgerEntry, LedgerEntryKind
from apps.merchants.models import BankAccount, Merchant
from apps.payouts.models import IdempotencyKey, Payout


# Stable UUIDs so the seeded merchants have predictable IDs in the demo
# URL. Generated from a fixed seed once and pasted here.
MERCHANTS = [
    {
        "id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
        "name": "Pixel & Pour Studio",
        "email": "ops@pixelandpour.in",
        "credits": [
            (1_500_00, "Acme Co. invoice #2034"),         # ₹1,500
            (4_750_50, "Bluebird Labs retainer Apr"),      # ₹4,750.50
            (8_900_00, "Cloud9 Inc. invoice #2036"),       # ₹8,900
        ],
        "bank": {
            "id": uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            "account_holder_name": "Pixel & Pour Studio Pvt Ltd",
            "account_number": "2891001234567",
            "ifsc": "HDFC0000123",
        },
    },
    {
        "id": uuid.UUID("22222222-2222-4222-8222-222222222222"),
        "name": "Mango Freelance",
        "email": "billing@mangofreelance.in",
        "credits": [
            (320_75, "Stripe-replaced invoice #11"),      # ₹320.75
            (1_280_00, "DesignDash invoice #14"),         # ₹1,280
        ],
        "bank": {
            "id": uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "account_holder_name": "Anika Verma",
            "account_number": "3120017654321",
            "ifsc": "ICIC0000456",
        },
    },
    {
        "id": uuid.UUID("33333333-3333-4333-8333-333333333333"),
        "name": "Rust Robotics Agency",
        "email": "ar@rustrobotics.in",
        "credits": [
            (12_000_00, "MetaCorp Q1 contract"),           # ₹12,000
            (3_400_00, "MetaCorp Q1 expense reimburse"),   # ₹3,400
            (75_50, "Refund test #1"),                     # ₹75.50
        ],
        "bank": {
            "id": uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
            "account_holder_name": "Rust Robotics LLP",
            "account_number": "5012009876543",
            "ifsc": "AXIS0000789",
        },
    },
]


class Command(BaseCommand):
    help = "Seed 3 demo merchants with credit history and a bank account each."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Wipe existing payouts/ledger/idempotency rows before seeding.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            self.stdout.write(self.style.WARNING("Resetting all data..."))
            IdempotencyKey.objects.all().delete()
            LedgerEntry.objects.all().delete()
            Payout.objects.all().delete()
            BankAccount.objects.all().delete()
            Merchant.objects.all().delete()

        with transaction.atomic():
            for spec in MERCHANTS:
                merchant, created = Merchant.objects.get_or_create(
                    id=spec["id"],
                    defaults={"name": spec["name"], "email": spec["email"]},
                )
                self.stdout.write(
                    f"{'created' if created else 'exists '} merchant {merchant.name} ({merchant.id})"
                )

                BankAccount.objects.get_or_create(
                    id=spec["bank"]["id"],
                    defaults={
                        "merchant": merchant,
                        "account_holder_name": spec["bank"]["account_holder_name"],
                        "account_number": spec["bank"]["account_number"],
                        "ifsc": spec["bank"]["ifsc"],
                        "is_primary": True,
                    },
                )

                # Don't double-credit on re-seed.
                if LedgerEntry.objects.filter(merchant=merchant).exists():
                    continue
                for amount_paise, descr in spec["credits"]:
                    LedgerEntry.objects.create(
                        merchant=merchant,
                        kind=LedgerEntryKind.CREDIT_CUSTOMER_PAYMENT,
                        amount_paise=amount_paise,
                        description=descr,
                    )

        self.stdout.write(self.style.SUCCESS("seed complete."))
