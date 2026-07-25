from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from users.tenant_models import TenantScopedManager


class TenantModel(models.Model):
    organization = models.ForeignKey('users.Organization', on_delete=models.PROTECT, related_name='+', db_index=True)
    tenant = models.ForeignKey('users.Organization', on_delete=models.PROTECT, related_name='+', db_index=True)

    class Meta:
        abstract = True

    def _sync_tenant(self):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id


class Supplier(TenantModel):
    company_name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=160, blank=True, default='')
    phone = models.CharField(max_length=40, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    physical_address = models.TextField(blank=True, default='')
    tin_vrn = models.CharField(max_length=80, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['company_name']
        constraints = [models.UniqueConstraint(fields=['tenant', 'company_name'], name='uniq_supplier_tenant_company')]
        indexes = [models.Index(fields=['tenant', 'is_active', 'company_name'])]

    def __str__(self):
        return self.company_name

    def save(self, *args, **kwargs):
        self._sync_tenant()
        self.company_name = self.company_name.strip()
        super().save(*args, **kwargs)

    @property
    def recorded_balance(self):
        purchases = self.purchases.filter(status=Purchase.Status.CONFIRMED).aggregate(total=models.Sum('total_cost'))['total'] or Decimal('0.00')
        payments = self.payment_records.aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
        return purchases - payments


class SupplierPaymentRecord(TenantModel):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='payment_records')
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=50, blank=True, default='')
    reference = models.CharField(max_length=100, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['-payment_date', '-id']

    def save(self, *args, **kwargs):
        self._sync_tenant()
        if self.supplier_id and self.supplier.tenant_id != self.tenant_id:
            raise ValidationError('Supplier payment must belong to the same tenant.')
        super().save(*args, **kwargs)


class Purchase(TenantModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchases')
    reference_number = models.CharField(max_length=100)
    purchase_date = models.DateField(db_index=True)
    notes = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_purchases')
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='confirmed_purchases', null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['-purchase_date', '-id']
        constraints = [models.UniqueConstraint(fields=['tenant', 'reference_number'], name='uniq_purchase_tenant_reference')]
        indexes = [models.Index(fields=['tenant', 'status', 'purchase_date'])]

    def __str__(self):
        return self.reference_number

    def save(self, *args, **kwargs):
        self._sync_tenant()
        if self.supplier_id and self.supplier.tenant_id != self.tenant_id:
            raise ValidationError('Purchase supplier must belong to the same tenant.')
        if self.pk:
            previous = type(self).objects.unscoped().filter(pk=self.pk).only('status').first()
            if previous and previous.status == self.Status.CONFIRMED and not getattr(self, '_service_update', False):
                raise ValidationError('Confirmed purchases are immutable.')
        super().save(*args, **kwargs)


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='purchase_lines')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)
    batch_reference = models.CharField(max_length=100, blank=True, default='')
    expiry_date = models.DateField(null=True, blank=True)
    serial_numbers = models.TextField(blank=True, default='', help_text='One serial number per line for serialized products.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    @property
    def line_total(self):
        return (self.quantity * self.unit_cost).quantize(Decimal('0.01'))

    def parsed_serial_numbers(self):
        return [value.strip() for value in self.serial_numbers.replace(',', '\n').splitlines() if value.strip()]

    def save(self, *args, **kwargs):
        if self.purchase_id and self.purchase.status == Purchase.Status.CONFIRMED:
            raise ValidationError('Confirmed purchase lines are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.purchase.status == Purchase.Status.CONFIRMED:
            raise ValidationError('Confirmed purchase lines are immutable.')
        return super().delete(*args, **kwargs)


class InventoryBalance(TenantModel):
    product = models.OneToOneField('products.Product', on_delete=models.PROTECT, related_name='inventory_balance')
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    average_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=0), name='inventory_balance_nonnegative')]
        indexes = [models.Index(fields=['tenant', 'quantity'])]

    def save(self, *args, **kwargs):
        self._sync_tenant()
        if self.product_id and self.product.tenant_id != self.tenant_id:
            raise ValidationError('Inventory balance product must belong to the same tenant.')
        super().save(*args, **kwargs)

    @property
    def total_value(self):
        return (self.quantity * self.average_cost).quantize(Decimal('0.01'))


class StockAdjustment(TenantModel):
    class Reason(models.TextChoices):
        DAMAGED = 'damaged', 'Damaged'
        EXPIRED = 'expired', 'Expired'
        LOST = 'lost', 'Lost'
        OPENING = 'opening_balance', 'Opening balance'
        CORRECTION = 'manual_correction', 'Manual correction'

    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='stock_adjustments')
    quantity_delta = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=30, choices=Reason.choices)
    notes = models.TextField(blank=True, default='')
    serial_numbers = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        self._sync_tenant()
        if self.pk:
            raise ValidationError('Stock adjustments are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Stock adjustments are immutable.')


class StockMovement(TenantModel):
    class MovementType(models.TextChoices):
        PURCHASE_IN = 'purchase_in', 'Purchase stock in'
        SALE_OUT = 'sale_out', 'Sale stock out'
        ADJUSTMENT_IN = 'adjustment_in', 'Adjustment in'
        ADJUSTMENT_OUT = 'adjustment_out', 'Adjustment out'
        OPENING = 'opening', 'Opening balance'

    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='stock_movements')
    movement_type = models.CharField(max_length=30, choices=MovementType.choices, db_index=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=2, help_text='Positive for stock in; negative for stock out.')
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    purchase_line = models.ForeignKey(PurchaseLine, on_delete=models.PROTECT, null=True, blank=True, related_name='stock_movements')
    billing_line = models.ForeignKey('billing.BillingLineItem', on_delete=models.PROTECT, null=True, blank=True, related_name='stock_movements')
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.PROTECT, null=True, blank=True, related_name='movements')
    batch_reference = models.CharField(max_length=100, blank=True, default='')
    expiry_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['tenant', 'product', 'created_at']), models.Index(fields=['tenant', 'movement_type', 'created_at'])]

    def save(self, *args, **kwargs):
        self._sync_tenant()
        if self.pk:
            raise ValidationError('Stock movements are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Stock movements are immutable.')


class StockUnit(TenantModel):
    class Status(models.TextChoices):
        AVAILABLE = 'available', 'Available'
        SOLD = 'sold', 'Sold'
        REMOVED = 'removed', 'Removed by adjustment'

    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='stock_units')
    serial_number = models.CharField(max_length=160)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE, db_index=True)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    batch_reference = models.CharField(max_length=100, blank=True, default='')
    expiry_date = models.DateField(null=True, blank=True)
    received_purchase_line = models.ForeignKey(PurchaseLine, on_delete=models.PROTECT, null=True, blank=True, related_name='stock_units')
    sold_billing_line = models.ForeignKey('billing.BillingLineItem', on_delete=models.PROTECT, null=True, blank=True, related_name='sold_stock_units')
    sold_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['serial_number']
        constraints = [models.UniqueConstraint(fields=['tenant', 'serial_number'], name='uniq_serial_number_per_tenant')]
        indexes = [models.Index(fields=['tenant', 'product', 'status'])]

    def __str__(self):
        return self.serial_number

    def save(self, *args, **kwargs):
        self._sync_tenant()
        self.serial_number = self.serial_number.strip().upper()
        super().save(*args, **kwargs)


class Cart(TenantModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        CONVERTED = 'converted', 'Converted'
        ABANDONED = 'abandoned', 'Abandoned'

    customer = models.ForeignKey('customers.Customer', on_delete=models.PROTECT, null=True, blank=True, related_name='inventory_carts')
    walk_in_name = models.CharField(max_length=200, blank=True, default='')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, default='')
    quotation = models.OneToOneField('billing.BillingDocument', on_delete=models.PROTECT, null=True, blank=True, related_name='source_inventory_cart_quotation')
    invoice = models.OneToOneField('billing.BillingDocument', on_delete=models.PROTECT, null=True, blank=True, related_name='source_inventory_cart_invoice')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inventory_carts')
    converted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['-updated_at']
        indexes = [models.Index(fields=['tenant', 'status', 'updated_at'])]

    def save(self, *args, **kwargs):
        self._sync_tenant()
        super().save(*args, **kwargs)

    @property
    def subtotal(self):
        return sum((line.line_total for line in self.lines.select_related('product')), Decimal('0.00'))


class CartLine(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='cart_lines')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1.00'))
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['cart', 'product'], name='uniq_cart_product')]

    @property
    def line_total(self):
        return max((self.quantity * self.unit_price - self.discount_amount).quantize(Decimal('0.01')), Decimal('0.00'))


class CartSerialSelection(models.Model):
    cart_line = models.ForeignKey(CartLine, on_delete=models.CASCADE, related_name='serial_selections')
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, related_name='cart_selections')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cart_line', 'stock_unit'], name='uniq_cart_line_serial')]


class InventorySale(TenantModel):
    invoice = models.OneToOneField('billing.BillingDocument', on_delete=models.PROTECT, related_name='inventory_sale')
    cart = models.OneToOneField(Cart, on_delete=models.PROTECT, null=True, blank=True, related_name='sale')
    receipt = models.OneToOneField('billing.BillingDocument', on_delete=models.PROTECT, null=True, blank=True, related_name='completed_inventory_sale')
    stock_deducted = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        indexes = [models.Index(fields=['tenant', 'stock_deducted', 'created_at'])]

    def save(self, *args, **kwargs):
        self._sync_tenant()
        super().save(*args, **kwargs)


class InventorySaleLine(models.Model):
    sale = models.ForeignKey(InventorySale, on_delete=models.PROTECT, related_name='lines')
    billing_line = models.OneToOneField('billing.BillingLineItem', on_delete=models.PROTECT, related_name='inventory_sale_line')
    cost_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    net_revenue = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def gross_profit(self):
        return self.net_revenue - self.cost_total


class DocumentSerialSelection(TenantModel):
    billing_line = models.ForeignKey('billing.BillingLineItem', on_delete=models.PROTECT, related_name='serial_selections')
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, related_name='document_selections')
    sold_at = models.DateTimeField(null=True, blank=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['billing_line', 'stock_unit'], name='uniq_document_line_serial'),
            models.UniqueConstraint(fields=['stock_unit'], condition=Q(sold_at__isnull=False), name='uniq_sold_document_serial'),
        ]

    def save(self, *args, **kwargs):
        self._sync_tenant()
        super().save(*args, **kwargs)


class InventorySettings(TenantModel):
    walk_in_customer_label = models.CharField(max_length=120, default='Walk-in Customer')
    dead_stock_days = models.PositiveIntegerField(default=90)
    fast_moving_days = models.PositiveIntegerField(default=30)
    fast_moving_min_units = models.PositiveIntegerField(default=10)
    slow_moving_days = models.PositiveIntegerField(default=30)
    slow_moving_max_units = models.PositiveIntegerField(default=2)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tenant'], name='uniq_inventory_settings_tenant')]

    def save(self, *args, **kwargs):
        self._sync_tenant()
        super().save(*args, **kwargs)


class ImportJob(TenantModel):
    class ImportType(models.TextChoices):
        PRODUCTS = 'products', 'Products'
        SUPPLIERS = 'suppliers', 'Suppliers'
        OPENING_STOCK = 'opening_stock', 'Opening stock'
        HISTORICAL_PURCHASES = 'historical_purchases', 'Historical purchases'
        HISTORICAL_SALES = 'historical_sales', 'Historical sales'

    class Status(models.TextChoices):
        VALIDATED = 'validated', 'Validated'
        COMMITTED = 'committed', 'Committed'
        FAILED = 'failed', 'Failed'

    import_type = models.CharField(max_length=30, choices=ImportType.choices)
    status = models.CharField(max_length=20, choices=Status.choices)
    file_name = models.CharField(max_length=255)
    affects_live_stock = models.BooleanField(default=False)
    row_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    validated_rows = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        self._sync_tenant()
        super().save(*args, **kwargs)


class HistoricalInventoryRecord(TenantModel):
    class RecordType(models.TextChoices):
        PURCHASE = 'purchase', 'Historical purchase'
        SALE = 'sale', 'Historical sale'

    record_type = models.CharField(max_length=20, choices=RecordType.choices, db_index=True)
    record_date = models.DateField(db_index=True)
    reference = models.CharField(max_length=120, blank=True, default='')
    sku = models.CharField(max_length=64, blank=True, default='')
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    unit_amount = models.DecimalField(max_digits=14, decimal_places=2)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.TextField(blank=True, default='')
    import_job = models.ForeignKey(ImportJob, on_delete=models.PROTECT, related_name='historical_records')
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['-record_date', '-id']
        indexes = [models.Index(fields=['tenant', 'record_type', 'record_date'])]

    def save(self, *args, **kwargs):
        self._sync_tenant()
        super().save(*args, **kwargs)
