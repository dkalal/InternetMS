from rest_framework import serializers

from customers.models import Customer


class IntegrationCustomerSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='name')
    customer_status = serializers.CharField(source='status')
    display_label = serializers.SerializerMethodField()
    contact_summary = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = [
            'uuid',
            'full_name',
            'phone',
            'email',
            'address',
            'customer_status',
            'customer_type',
            'created_at',
            'display_label',
            'contact_summary',
        ]

    def get_display_label(self, obj):
        contact_bits = [bit for bit in [obj.phone, obj.email] if bit]
        if contact_bits:
            return f"{obj.name} ({' | '.join(contact_bits)})"
        return obj.name

    def get_contact_summary(self, obj):
        contact_bits = []
        if obj.phone:
            contact_bits.append(f"Phone: {obj.phone}")
        if obj.email:
            contact_bits.append(f"Email: {obj.email}")
        if obj.address:
            contact_bits.append(f"Address: {obj.address}")
        return ' | '.join(contact_bits) if contact_bits else ''
