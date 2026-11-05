"""Physical divisible containers and append-only provenance (not serial units)."""
from uuid import uuid4
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Floor
from users.tenant_models import TenantScopedManager
from .models import TenantModel


class PackagingActivation(TenantModel):
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='packaging_activations')
    request_key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    enabled = models.BooleanField()
    version = models.PositiveIntegerField()
    reason = models.CharField(max_length=500)
    base_quantity = models.DecimalField(max_digits=16, decimal_places=6)
    average_cost = models.DecimalField(max_digits=16, decimal_places=6)
    inventory_value = models.DecimalField(max_digits=20, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'request_key'], name='activation_tenant_request'),
            models.CheckConstraint(condition=Q(tenant=F('organization')), name='activation_tenant_matches'),
            models.CheckConstraint(condition=Q(base_quantity__gte=0, base_quantity__lt=10000000000, average_cost__gte=0, average_cost__lt=10000000000, inventory_value__gte=0, inventory_value__lt=1000000000000000000), name='activation_values_nonnegative'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Activation reconciliation history is immutable.')
        self._sync_tenant()
        if self.product.tenant_id != self.tenant_id:
            raise ValidationError('Activation product belongs to another tenant.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Activation history cannot be deleted.')


class StockContainer(TenantModel):
    class State(models.TextChoices):
        SEALED = 'sealed', 'Sealed'
        OPENED = 'opened', 'Opened'
        DEPLETED = 'depleted', 'Depleted'
        RETURNED_PIECE = 'returned_piece', 'Returned piece'
        RETURNED_FULL = 'returned_full', 'Returned full container'
        WRITTEN_OFF = 'written_off', 'Written off'

    label = models.UUIDField(default=uuid4, editable=False)
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='stock_containers')
    packaging = models.ForeignKey('products.ProductPackaging', on_delete=models.PROTECT, null=True, blank=True)
    purchase_line = models.ForeignKey('inventory.PurchaseLine', on_delete=models.PROTECT, null=True, blank=True, related_name='containers')
    activation = models.ForeignKey(PackagingActivation, on_delete=models.PROTECT, null=True, blank=True, related_name='containers')
    returned_allocation = models.ForeignKey('inventory.ContainerAllocation', on_delete=models.PROTECT, null=True, blank=True, related_name='returned_containers')
    capacity = models.DecimalField(max_digits=16, decimal_places=6)
    original_quantity = models.DecimalField(max_digits=16, decimal_places=6)
    remaining_quantity = models.DecimalField(max_digits=16, decimal_places=6)
    supplier_identifier = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=20, choices=State.choices)
    received_at = models.DateTimeField()
    opened_at = models.DateTimeField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'label'], name='container_tenant_label'),
            models.CheckConstraint(condition=Q(tenant=F('organization')), name='container_tenant_matches'),
            models.CheckConstraint(condition=Q(capacity__gt=0, capacity__lt=10000000000, original_quantity__gt=0,
                remaining_quantity__gte=0, original_quantity__lte=F('capacity'), remaining_quantity__lte=F('original_quantity'),
                capacity=Floor('capacity'), original_quantity=Floor('original_quantity'), remaining_quantity=Floor('remaining_quantity')),
                name='container_quantity_bounds'),
            models.CheckConstraint(condition=(Q(purchase_line__isnull=False, activation__isnull=True, returned_allocation__isnull=True)
                | Q(purchase_line__isnull=True, activation__isnull=False, returned_allocation__isnull=True)
                | Q(purchase_line__isnull=True, activation__isnull=True, returned_allocation__isnull=False)), name='container_one_source'),
            models.CheckConstraint(condition=~Q(state='sealed') | Q(packaging__isnull=False, opened_at__isnull=True,
                original_quantity=F('capacity'), remaining_quantity=F('capacity'), returned_allocation__isnull=True), name='container_sealed_intact'),
            models.CheckConstraint(condition=~Q(state__in=['depleted', 'written_off']) | Q(remaining_quantity=0), name='container_terminal_empty'),
            models.CheckConstraint(condition=Q(state__in=['sealed', 'opened', 'depleted', 'returned_piece', 'returned_full', 'written_off']), name='container_known_state'),
        ]
        indexes = [models.Index(fields=['tenant', 'product', 'state', 'received_at'])]

    def clean(self):
        super().clean()
        if self.product.tenant_id != self.tenant_id:
            raise ValidationError('Container product belongs to another tenant.')
        if self.packaging_id and (self.packaging.tenant_id != self.tenant_id or self.packaging.product_id != self.product_id
                                  or self.packaging.factor != self.capacity):
            raise ValidationError('Container packaging must match its product, tenant and original capacity.')
        if self.returned_allocation_id and self.state not in {self.State.RETURNED_PIECE, self.State.RETURNED_FULL, self.State.DEPLETED, self.State.WRITTEN_OFF}:
            raise ValidationError('Returned container state must preserve its return provenance.')
        if self.state in {self.State.RETURNED_PIECE, self.State.RETURNED_FULL} and not self.returned_allocation_id:
            raise ValidationError('Returned containers require original sold allocation provenance.')
        for source in (self.purchase_line, self.activation, self.returned_allocation):
            if source is not None and (source.tenant_id != self.tenant_id or source.product_id != self.product_id):
                raise ValidationError('Container provenance belongs to another product or tenant.')
        if self.pk:
            old = type(self).objects.unscoped().get(pk=self.pk)
            fixed = ('tenant_id', 'organization_id', 'label', 'product_id', 'packaging_id', 'purchase_line_id',
                     'activation_id', 'returned_allocation_id', 'capacity', 'original_quantity', 'received_at', 'supplier_identifier', 'expiry_date')
            if any(getattr(old, f) != getattr(self, f) for f in fixed):
                raise ValidationError('Container identity and provenance are immutable.')
            if old.opened_at and self.opened_at != old.opened_at:
                raise ValidationError('A container opening event cannot be erased or changed.')
            if old.state != self.State.SEALED and self.state == self.State.SEALED:
                raise ValidationError('An opened or returned container cannot become sealed.')
            if self.remaining_quantity > old.remaining_quantity:
                raise ValidationError('Returned stock requires a new physical container; existing lengths cannot be refilled.')
            if not getattr(self, '_container_service_update', False):
                raise ValidationError('Container balances must use the owning inventory service.')

    def save(self, *args, **kwargs):
        self._sync_tenant()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Container history cannot be deleted.')


class ContainerAllocation(TenantModel):
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='+')
    container = models.ForeignKey(StockContainer, on_delete=models.PROTECT, related_name='allocations')
    billing_line = models.ForeignKey('billing.BillingLineItem', on_delete=models.PROTECT, related_name='container_allocations')
    movement = models.ForeignKey('inventory.StockMovement', on_delete=models.PROTECT, related_name='container_allocations')
    base_quantity = models.DecimalField(max_digits=16, decimal_places=6)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=6)
    cost_total = models.DecimalField(max_digits=16, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['movement', 'container'], name='allocation_movement_container'),
            models.CheckConstraint(condition=Q(tenant=F('organization')), name='allocation_tenant_matches'),
            models.CheckConstraint(condition=Q(base_quantity__gt=0, base_quantity__lt=10000000000, base_quantity=Floor('base_quantity'), unit_cost__gte=0, unit_cost__lt=10000000000, cost_total__gte=0, cost_total__lt=100000000000000), name='allocation_quantity_cost'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Posted allocations are immutable.')
        self._sync_tenant()
        for source in (self.product, self.container, self.billing_line, self.movement):
            if source.tenant_id != self.tenant_id or getattr(source, 'product_id', self.product_id) != self.product_id:
                raise ValidationError('Allocation relationships must belong to the same product and tenant.')
        if self.movement.billing_line_id != self.billing_line_id or self.movement.quantity >= 0:
            raise ValidationError('Allocation must refer to its stock-out line movement.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Posted allocations cannot be deleted.')


class ProvisionalContainerAllocation(TenantModel):
    container = models.ForeignKey(StockContainer, on_delete=models.PROTECT, related_name='provisional_allocations')
    cart_line = models.ForeignKey('inventory.CartLine', on_delete=models.CASCADE, null=True, blank=True, related_name='container_selections')
    billing_line = models.ForeignKey('billing.BillingLineItem', on_delete=models.PROTECT, null=True, blank=True, related_name='container_selections')
    base_quantity = models.DecimalField(max_digits=16, decimal_places=6)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(tenant=F('organization')), name='selection_tenant_matches'),
            models.CheckConstraint(condition=Q(cart_line__isnull=False, billing_line__isnull=True)
                | Q(cart_line__isnull=True, billing_line__isnull=False), name='selection_one_line'),
            models.CheckConstraint(condition=Q(base_quantity__gt=0, base_quantity__lt=10000000000, base_quantity=Floor('base_quantity')), name='selection_quantity_positive'),
            models.UniqueConstraint(fields=['cart_line', 'container'], name='selection_cart_container'),
            models.UniqueConstraint(fields=['billing_line', 'container'], name='selection_bill_container'),
        ]

    def _validate_parent(self):
        line = self.cart_line if self.cart_line_id else self.billing_line
        if self.cart_line_id and line.cart.status != 'draft':
            raise ValidationError('Only draft cart allocations can be changed.')
        if self.billing_line_id and line.document.status in {'paid', 'void', 'superseded', 'cancelled', 'converted'}:
            raise ValidationError('Completed document allocations cannot be changed.')
        if self.pk:
            old = type(self).objects.unscoped().get(pk=self.pk)
            if (old.cart_line_id, old.billing_line_id, old.tenant_id) != (self.cart_line_id, self.billing_line_id, self.tenant_id):
                raise ValidationError('A provisional allocation cannot be reassigned to another line or tenant.')
        return line

    def save(self, *args, **kwargs):
        self._sync_tenant()
        line = self._validate_parent()
        if not line or line.tenant_id != self.tenant_id or self.container.tenant_id != self.tenant_id or self.container.product_id != line.product_id:
            raise ValidationError('Provisional allocation must match the line product and tenant.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._validate_parent()
        return super().delete(*args, **kwargs)
