from django.urls import path

from . import views

app_name = "payouts"

urlpatterns = [
    path("payouts", views.create_payout, name="create"),
    path("payouts/list", views.list_payouts, name="list"),
    path("payouts/<uuid:payout_id>", views.get_payout, name="detail"),
]
