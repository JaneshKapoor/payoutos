from django.urls import path

from . import views

app_name = "merchants"

urlpatterns = [
    path("merchants", views.list_merchants, name="list"),
    path("merchants/<uuid:merchant_id>/balance", views.merchant_balance, name="balance"),
    path("merchants/<uuid:merchant_id>/ledger", views.merchant_ledger, name="ledger"),
    path(
        "merchants/<uuid:merchant_id>/bank-accounts",
        views.merchant_bank_accounts,
        name="bank-accounts",
    ),
]
