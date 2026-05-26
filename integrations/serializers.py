from rest_framework import serializers

from customers.models import Customer


class IntegrationCustomerSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='name')
    customer_status = serializers.CharField(source='status')

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
        ]

