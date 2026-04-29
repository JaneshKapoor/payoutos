"""
HTTP layer.

The view's only job is to:
  1. Read & validate input.
  2. Call into services.request_payout.
  3. Map domain errors → HTTP responses.
The interesting logic — locking, idempotency, ledger writes — lives in
services.py.
"""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from .models import Payout
from .serializers import PayoutCreateSerializer, PayoutSerializer
from .services import (
    IdempotencyKeyConflict,
    IdempotencyKeyInFlight,
    InsufficientFunds,
    InvalidBankAccount,
    PayoutError,
    request_payout,
)

logger = logging.getLogger(__name__)


def _merchant_id_from_request(request: Request) -> str | None:
    """Tiny auth shim. In a real system we'd resolve this from a session
    or API key; for the challenge we accept it as a header so the demo
    UI can switch merchants without re-auth."""
    return request.headers.get("X-Merchant-Id") or request.query_params.get(
        "merchant_id"
    )


@api_view(["POST"])
def create_payout(request: Request) -> Response:
    merchant_id = _merchant_id_from_request(request)
    if not merchant_id:
        return Response(
            {"error": "merchant_id_required",
             "detail": "X-Merchant-Id header is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    idem_key = request.headers.get("Idempotency-Key", "").strip()
    if not idem_key:
        return Response(
            {"error": "idempotency_key_required",
             "detail": "Idempotency-Key header is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    payload = PayoutCreateSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    body = payload.validated_data

    try:
        result = request_payout(
            merchant_id=merchant_id,
            bank_account_id=body["bank_account_id"],
            amount_paise=body["amount_paise"],
            idempotency_key=idem_key,
            request_body={
                "amount_paise": body["amount_paise"],
                "bank_account_id": str(body["bank_account_id"]),
            },
        )
    except (InsufficientFunds, IdempotencyKeyConflict, InvalidBankAccount) as e:
        return Response(
            {"error": e.code, "detail": str(e)}, status=e.http_status
        )
    except IdempotencyKeyInFlight as e:
        return Response(
            {"error": e.code, "detail": str(e)}, status=e.http_status
        )
    except PayoutError as e:
        return Response(
            {"error": e.code, "detail": str(e)}, status=e.http_status
        )

    payout = result.payout
    body = PayoutSerializer(payout).data
    response = Response(
        body,
        status=status.HTTP_200_OK if result.cached else status.HTTP_201_CREATED,
    )
    if result.cached:
        response["Idempotent-Replayed"] = "true"
    return response


@api_view(["GET"])
def list_payouts(request: Request) -> Response:
    merchant_id = _merchant_id_from_request(request)
    if not merchant_id:
        return Response(
            {"error": "merchant_id_required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    limit = min(int(request.query_params.get("limit", 50)), 200)
    qs = Payout.objects.filter(merchant_id=merchant_id).order_by("-created_at")[:limit]
    return Response({"payouts": PayoutSerializer(qs, many=True).data})


@api_view(["GET"])
def get_payout(request: Request, payout_id: str) -> Response:
    try:
        payout = Payout.objects.get(id=payout_id)
    except Payout.DoesNotExist:
        return Response(
            {"error": "not_found"}, status=status.HTTP_404_NOT_FOUND
        )
    return Response(PayoutSerializer(payout).data)
