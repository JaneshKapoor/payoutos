from rest_framework import serializers

from .models import Payout


class PayoutCreateSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.UUIDField()


class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = (
            "id",
            "merchant",
            "bank_account",
            "amount_paise",
            "state",
            "attempts",
            "failure_reason",
            "failure_detail",
            "created_at",
            "updated_at",
            "completed_at",
            "last_attempted_at",
        )
