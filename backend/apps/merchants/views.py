"""Merchant read APIs that the dashboard needs:
  * list merchants (for the demo merchant switcher)
  * GET /merchants/:id/balance      → BalanceSnapshot
  * GET /merchants/:id/ledger       → recent ledger entries
  * GET /merchants/:id/bank-accounts → bank accounts
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.ledger.models import LedgerEntry
from apps.ledger.serializers import LedgerEntrySerializer
from apps.ledger.services import get_balance

from .models import BankAccount, Merchant
from .serializers import BankAccountSerializer, MerchantSerializer


@api_view(["GET"])
def list_merchants(_request: Request) -> Response:
    qs = Merchant.objects.all()
    return Response({"merchants": MerchantSerializer(qs, many=True).data})


@api_view(["GET"])
def merchant_balance(_request: Request, merchant_id: str) -> Response:
    if not Merchant.objects.filter(id=merchant_id).exists():
        return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
    snap = get_balance(merchant_id)
    return Response(
        {
            "merchant_id": merchant_id,
            "available_paise": snap.available_paise,
            "held_paise": snap.held_paise,
            "settled_paise": snap.settled_paise,
            "lifetime_credits_paise": snap.lifetime_credits_paise,
            "lifetime_debits_paise": snap.lifetime_debits_paise,
        }
    )


@api_view(["GET"])
def merchant_ledger(request: Request, merchant_id: str) -> Response:
    if not Merchant.objects.filter(id=merchant_id).exists():
        return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
    limit = min(int(request.query_params.get("limit", 50)), 200)
    qs = LedgerEntry.objects.filter(merchant_id=merchant_id).order_by(
        "-created_at"
    )[:limit]
    return Response({"entries": LedgerEntrySerializer(qs, many=True).data})


@api_view(["GET"])
def merchant_bank_accounts(_request: Request, merchant_id: str) -> Response:
    if not Merchant.objects.filter(id=merchant_id).exists():
        return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
    qs = BankAccount.objects.filter(merchant_id=merchant_id)
    return Response({"bank_accounts": BankAccountSerializer(qs, many=True).data})
