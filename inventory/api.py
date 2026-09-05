from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers, status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import BillingDocument, BillingLineItem
from billing.services import BillingService, BillingServiceError, LineItemInput
from customers.models import Customer
from integrations.services import resolve_integration_consumer
from products.models import Product, ProductCategory
from users.permissions import (
    PermissionCode,
    discount_authorization_error,
    has_tenant_permission,
    membership_for,
    permission_grant_for,
    sales_document_queryset_for,
)

from .models import DocumentSerialSelection, InventoryBalance, StockMovement, StockUnit, Supplier
from .services import CartService


class InventoryAPIPermission(BasePermission):
    message = 'An active tenant integration token with inventory API permission is required.'

    def has_permission(self, request, view):
        consumer = resolve_integration_consumer(request)
        if consumer is None or not has_tenant_permission(request.user, consumer.organization, PermissionCode.INVENTORY_API):
            return False
        required = getattr(view, 'required_permission', None)
        return required is None or has_tenant_permission(request.user, consumer.organization, required)


def _api_allowed(request, code):
    consumer = resolve_integration_consumer(request)
    return consumer is not None and has_tenant_permission(request.user, consumer.organization, code)


class ProductSerializer(serializers.ModelSerializer):
    available_stock = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Product
        fields = ['id', 'sku', 'name', 'description', 'item_type', 'catalog_category', 'brand', 'model_number', 'buying_price', 'selling_price', 'technician_price', 'tax_eligible', 'track_stock', 'is_serialized', 'track_expiry', 'reorder_threshold', 'is_active', 'available_stock']
        read_only_fields = ['available_stock']

    def get_fields(self):
        fields = super().get_fields()
        if not _api_allowed(self.context['request'], PermissionCode.COST_REPORT_VIEW):
            fields.pop('buying_price', None)
        if not _api_allowed(self.context['request'], PermissionCode.PRODUCT_MANAGE):
            fields.pop('technician_price', None)
        return fields

    def validate(self, attrs):
        organization = resolve_integration_consumer(self.context['request']).organization
        category = attrs.get('catalog_category')
        if category and category.tenant_id != organization.id:
            raise serializers.ValidationError({'catalog_category': 'Category belongs to another tenant.'})
        sku = (attrs.get('sku') or getattr(self.instance, 'sku', '')).strip().upper()
        query = Product.objects.unscoped().filter(tenant=organization, sku__iexact=sku)
        if self.instance:
            query = query.exclude(pk=self.instance.pk)
        if not sku or query.exists():
            raise serializers.ValidationError({'sku': 'A unique SKU is required.'})
        attrs['sku'] = sku
        return attrs

    def create(self, validated_data):
        organization = resolve_integration_consumer(self.context['request']).organization
        return Product.objects.create(organization=organization, tenant=organization, quantity=0, stock=0, measure_unit='Unit', **validated_data)

    def update(self, instance, validated_data):
        protected = {'quantity', 'stock'}
        for key, value in validated_data.items():
            if key not in protected:
                setattr(instance, key, value)
        instance.save()
        return instance


class SupplierSerializer(serializers.ModelSerializer):
    recorded_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Supplier
        fields = ['id', 'company_name', 'contact_person', 'phone', 'email', 'physical_address', 'tin_vrn', 'notes', 'is_active', 'recorded_balance']

    def create(self, validated_data):
        request = self.context['request']
        organization = resolve_integration_consumer(request).organization
        return Supplier.objects.create(organization=organization, tenant=organization, created_by=request.user, **validated_data)


class StockSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source='product.sku')
    product_name = serializers.CharField(source='product.name')

    class Meta:
        model = InventoryBalance
        fields = ['product_id', 'sku', 'product_name', 'quantity', 'average_cost', 'updated_at']

    def get_fields(self):
        fields = super().get_fields()
        if not _api_allowed(self.context['request'], PermissionCode.COST_REPORT_VIEW):
            fields.pop('average_cost', None)
        return fields


class StockMovementSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source='product.sku')
    product_name = serializers.CharField(source='product.name')

    class Meta:
        model = StockMovement
        fields = ['id', 'sku', 'product_name', 'movement_type', 'quantity', 'balance_after', 'unit_cost', 'batch_reference', 'expiry_date', 'created_at']

    def get_fields(self):
        fields = super().get_fields()
        if not _api_allowed(self.context['request'], PermissionCode.COST_REPORT_VIEW):
            fields.pop('unit_cost', None)
        return fields


class InvoiceLineSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source='product.sku', allow_null=True)
    serial_numbers = serializers.SerializerMethodField()

    class Meta:
        model = BillingLineItem
        fields = ['id', 'sku', 'description', 'quantity', 'unit_price', 'discount_amount', 'line_total', 'serial_numbers']

    def get_serial_numbers(self, obj):
        return list(obj.serial_selections.values_list('stock_unit__serial_number', flat=True))


class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source='customer.name')

    class Meta:
        model = BillingDocument
        fields = ['id', 'number', 'customer_name', 'sale_pricing_category', 'issue_date', 'status', 'currency', 'subtotal', 'discount_amount', 'tax_rate', 'tax_amount', 'total', 'items']


class InvoiceItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = serializers.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    serial_numbers = serializers.ListField(child=serializers.CharField(), required=False, default=list)


class InvoiceCreateSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField(required=False, allow_null=True)
    walk_in_name = serializers.CharField(required=False, allow_blank=True)
    sale_pricing_category = serializers.ChoiceField(
        choices=(
            BillingDocument.SalePricingCategory.CUSTOMER_TIER,
            BillingDocument.SalePricingCategory.STANDARD,
            BillingDocument.SalePricingCategory.TECHNICIAN,
            BillingDocument.SalePricingCategory.WHOLESALE,
        ),
        default=BillingDocument.SalePricingCategory.CUSTOMER_TIER,
    )
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    discount_amount = serializers.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    status = serializers.ChoiceField(choices=[BillingDocument.Status.DRAFT, BillingDocument.Status.ISSUED], default=BillingDocument.Status.DRAFT)
    notes = serializers.CharField(required=False, allow_blank=True)
    items = InvoiceItemInputSerializer(many=True)

    def create(self, validated_data):
        request = self.context['request']
        organization = resolve_integration_consumer(request).organization
        customer_id = validated_data.get('customer_id')
        if customer_id is None:
            customer_id = CartService._walk_in_customer(organization=organization, label=validated_data.get('walk_in_name', '')).pk
        customer = Customer.all_objects.filter(pk=customer_id, tenant=organization, is_deleted=False).first()
        if customer is None:
            raise serializers.ValidationError({'customer_id': 'Invalid customer.'})
        inputs = []
        products = []
        sale_pricing_category = validated_data['sale_pricing_category']
        membership = membership_for(request.user, organization)
        if sale_pricing_category != BillingDocument.SalePricingCategory.CUSTOMER_TIER and not has_tenant_permission(request.user, organization, PermissionCode.CART_PRICING_OVERRIDE, membership=membership):
            raise serializers.ValidationError({'sale_pricing_category': 'You cannot override the customer category.'})
        pricing_grant = permission_grant_for(membership, PermissionCode.CART_PRICING_OVERRIDE)
        if pricing_grant is not None and sale_pricing_category not in {'customer_tier', *pricing_grant.allowed_pricing_categories}:
            raise serializers.ValidationError({'sale_pricing_category': 'This customer category is outside your allowed categories.'})
        requested_tax_rate = validated_data['tax_rate']
        if requested_tax_rate != Decimal('0.00') and not has_tenant_permission(request.user, organization, PermissionCode.CART_TAX_RATE_EDIT, membership=membership):
            raise serializers.ValidationError({'tax_rate': 'You do not have permission to edit the tax rate.'})
        effective_category = (
            customer.default_sale_pricing_category
            if sale_pricing_category == BillingDocument.SalePricingCategory.CUSTOMER_TIER
            else sale_pricing_category
        )
        for item in validated_data['items']:
            product = Product.objects.unscoped().filter(pk=item['product_id'], tenant=organization, is_active=True).first()
            if product is None:
                raise serializers.ValidationError({'items': f"Invalid product {item['product_id']}."})
            if item['quantity'] <= 0:
                raise serializers.ValidationError({'items': 'Quantities must be positive.'})
            inputs.append(LineItemInput(
                product_id=product.pk, description=product.name, quantity=item['quantity'],
                unit_price=product.price_for_sale_category(
                    sale_pricing_category=effective_category, quantity=item['quantity'],
                ),
                discount_amount=item['discount_amount'], pricing_mode=effective_category,
            ))
            products.append((product, item.get('serial_numbers', [])))
        requested_discount = validated_data['discount_amount'] + sum((item.discount_amount for item in inputs), Decimal('0.00'))
        subtotal = sum((item.quantity * item.unit_price for item in inputs), Decimal('0.00'))
        discount_error = discount_authorization_error(
            membership,
            gross_subtotal=subtotal,
            total_discount=requested_discount,
        )
        if discount_error:
            raise serializers.ValidationError({'discount_amount': discount_error})
        try:
            invoice = BillingService.create_document(
                organization=organization, created_by=request.user, document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=customer_id, status=validated_data['status'], tax_rate=validated_data['tax_rate'],
                discount_amount=validated_data['discount_amount'], notes=validated_data.get('notes', ''), items=inputs,
                sale_pricing_category=sale_pricing_category,
            )
        except BillingServiceError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        for line, (product, serial_numbers) in zip(invoice.items.order_by('id'), products):
            normalized = [value.strip().upper() for value in serial_numbers]
            if product.is_serialized:
                units = list(StockUnit.objects.unscoped().filter(
                    tenant=organization, product=product, status=StockUnit.Status.AVAILABLE, serial_number__in=normalized
                ))
                if len(units) != int(line.quantity) or len(units) != len(normalized):
                    raise serializers.ValidationError({'items': f'Select one available serial for every {product.name} unit.'})
                DocumentSerialSelection.objects.bulk_create([
                    DocumentSerialSelection(organization=organization, tenant=organization, billing_line=line, stock_unit=unit) for unit in units
                ])
        return invoice


class ProductListCreateAPI(generics.ListCreateAPIView):
    permission_classes = [InventoryAPIPermission]
    required_permission = PermissionCode.PRODUCT_VIEW
    serializer_class = ProductSerializer

    def get_queryset(self):
        return Product.objects.unscoped().filter(tenant=resolve_integration_consumer(self.request).organization).order_by('name')

    def post(self, request, *args, **kwargs):
        if not _api_allowed(request, PermissionCode.PRODUCT_MANAGE):
            return Response({'detail': 'Product management permission is required.'}, status=403)
        return super().post(request, *args, **kwargs)


class ProductDetailAPI(generics.RetrieveUpdateAPIView):
    permission_classes = [InventoryAPIPermission]
    required_permission = PermissionCode.PRODUCT_VIEW
    serializer_class = ProductSerializer

    def get_queryset(self):
        return Product.objects.unscoped().filter(tenant=resolve_integration_consumer(self.request).organization)

    def update(self, request, *args, **kwargs):
        if not _api_allowed(request, PermissionCode.PRODUCT_MANAGE):
            return Response({'detail': 'Product management permission is required.'}, status=403)
        return super().update(request, *args, **kwargs)


class SupplierListCreateAPI(generics.ListCreateAPIView):
    permission_classes = [InventoryAPIPermission]
    required_permission = PermissionCode.SUPPLIER_MANAGE
    serializer_class = SupplierSerializer

    def get_queryset(self):
        return Supplier.objects.unscoped().filter(tenant=resolve_integration_consumer(self.request).organization)


class SupplierDetailAPI(generics.RetrieveUpdateAPIView):
    permission_classes = [InventoryAPIPermission]
    required_permission = PermissionCode.SUPPLIER_MANAGE
    serializer_class = SupplierSerializer

    def get_queryset(self):
        return Supplier.objects.unscoped().filter(tenant=resolve_integration_consumer(self.request).organization)


class StockListAPI(generics.ListAPIView):
    permission_classes = [InventoryAPIPermission]
    required_permission = PermissionCode.STOCK_VIEW
    serializer_class = StockSerializer

    def get_queryset(self):
        return InventoryBalance.objects.unscoped().filter(tenant=resolve_integration_consumer(self.request).organization).select_related('product')


class MovementListAPI(generics.ListAPIView):
    permission_classes = [InventoryAPIPermission]
    required_permission = PermissionCode.STOCK_MOVEMENT_VIEW
    serializer_class = StockMovementSerializer

    def get_queryset(self):
        return StockMovement.objects.unscoped().filter(tenant=resolve_integration_consumer(self.request).organization).select_related('product')


class InvoiceListCreateAPI(generics.ListCreateAPIView):
    permission_classes = [InventoryAPIPermission]
    required_permission = PermissionCode.BILLING_CREATE

    def get_serializer_class(self):
        return InvoiceCreateSerializer if self.request.method == 'POST' else InvoiceSerializer

    def get_queryset(self):
        organization = resolve_integration_consumer(self.request).organization
        return sales_document_queryset_for(self.request.user, organization).filter(
            document_type=BillingDocument.DocumentType.INVOICE,
            inventory_sale__isnull=False,
        ).select_related('customer').prefetch_related('items__product')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = serializer.save()
        return Response(InvoiceSerializer(invoice, context={'request': request}).data, status=status.HTTP_201_CREATED)


class InvoicePaymentAPI(APIView):
    permission_classes = [InventoryAPIPermission]
    required_permission = PermissionCode.PAYMENT_REGISTER

    def post(self, request, pk):
        organization = resolve_integration_consumer(request).organization
        invoice = sales_document_queryset_for(request.user, organization).filter(
            pk=pk, document_type=BillingDocument.DocumentType.INVOICE
        ).first()
        if invoice is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            receipt = BillingService.create_receipt_from_invoice(
                organization=organization, created_by=request.user, invoice_id=pk,
                amount_paid=Decimal(str(request.data.get('amount_paid', '0'))),
                payment_method=str(request.data.get('payment_method', '')),
                payment_reference=str(request.data.get('payment_reference', '')),
                notes=str(request.data.get('notes', '')),
            )
        except (BillingServiceError, ValueError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'receipt_id': receipt.pk, 'receipt_number': receipt.number, 'amount': str(receipt.total)}, status=status.HTTP_201_CREATED)
