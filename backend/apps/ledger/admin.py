from django.contrib import admin

from .models import LedgerEntry


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "merchant", "kind", "amount_paise", "payout", "created_at")
    list_filter = ("kind",)
    search_fields = ("merchant__name", "description")
    readonly_fields = (
        "id",
        "merchant",
        "kind",
        "amount_paise",
        "payout",
        "description",
        "created_at",
    )
