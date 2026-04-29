from django.contrib import admin

from .models import IdempotencyKey, Payout


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "merchant",
        "amount_paise",
        "state",
        "attempts",
        "created_at",
        "completed_at",
    )
    list_filter = ("state", "failure_reason")
    search_fields = ("id", "merchant__name", "merchant__email")
    readonly_fields = (
        "id",
        "merchant",
        "bank_account",
        "amount_paise",
        "state",
        "attempts",
        "last_attempted_at",
        "failure_reason",
        "failure_detail",
        "created_at",
        "updated_at",
        "completed_at",
    )


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("id", "merchant", "key", "status", "payout", "created_at")
    list_filter = ("status",)
    search_fields = ("key",)
