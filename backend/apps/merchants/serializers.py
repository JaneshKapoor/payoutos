from rest_framework import serializers

from .models import BankAccount, Merchant


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = (
            "id",
            "account_holder_name",
            "account_number_masked",
            "ifsc",
            "is_primary",
            "created_at",
        )

    account_number_masked = serializers.SerializerMethodField()

    def get_account_number_masked(self, obj: BankAccount) -> str:
        return f"****{obj.account_number[-4:]}" if obj.account_number else ""


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        fields = ("id", "name", "email", "created_at")
