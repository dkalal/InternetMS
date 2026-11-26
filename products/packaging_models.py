"""Product-specific commercial packages, independent of preservation-only fields."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Floor

from users.tenant_models import TenantScopedManager


class ProductPackaging(models.Model):
    tenant = models.ForeignKey('users.Organization', on_delete=models.PROTECT, related_name='+')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='packaging_definitions')
    unit = models.ForeignKey('products.UnitOfMeasure', on_delete=models.PROTECT, related_name='product_packaging')
    version = models.PositiveIntegerField()
    factor = models.DecimalField(max_digits=16, decimal_places=6)
    standard_price = models.DecimalField(max_digits=12, decimal_places=2)
    technician_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    wholesale_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    retail_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['product', 'version'], name='pack_product_version_unique'),
            models.CheckConstraint(condition=Q(factor__gt=0, factor__lt=10000000000, factor=Floor('factor')), name='pack_factor_positive_whole'),
            models.CheckConstraint(condition=Q(version__gt=0), name='pack_version_positive'),
            models.CheckConstraint(condition=Q(standard_price__gte=0, standard_price__lt=10000000000), name='pack_standard_nonnegative'),
            *[models.CheckConstraint(condition=Q(**{field + '__isnull': True}) | Q(**{field + '__gte': 0, field + '__lt': 10000000000}),
                                    name='pack_' + field.split('_')[0] + '_nonnegative')
              for field in ('technician_price', 'wholesale_price', 'retail_price')],
        ]
        indexes = [models.Index(fields=['tenant', 'product', 'is_active'])]

    def clean(self):
        super().clean()
        from .models import Product
        if self.product_id and self.product.tenant_id != self.tenant_id:
            raise ValidationError('Packaging product belongs to another tenant.')
        if self.unit_id and (self.unit.tenant_id != self.tenant_id or not self.unit.is_active):
            raise ValidationError('Select an active unit in the same tenant.')
        if self.product_id and (self.product.item_type != Product.ItemType.PHYSICAL
                                or not self.product.track_stock or self.product.is_serialized):
            raise ValidationError('Packaging requires a non-serialized stock-tracked physical product.')
        if self.product_id and self.unit_id == self.product.sales_unit_id:
            raise ValidationError('Packaging unit must differ from the base inventory unit.')
        if self.pk:
            old = type(self).objects.unscoped().get(pk=self.pk)
            if any(getattr(old, f) != getattr(self, f) for f in ('tenant_id', 'product_id', 'unit_id', 'factor', 'version')):
                raise ValidationError('Packaging identity and factor are immutable. Create a new revision.')
            prices_changed = any(getattr(old, field) != getattr(self, field) for field in ('standard_price', 'technician_price', 'wholesale_price', 'retail_price', 'is_active'))
            if prices_changed and not getattr(self, '_packaging_service_update', False):
                raise ValidationError('Packaging prices and retirement must use the authorized service.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Retire packaging through its service; do not delete configuration history.')

    def price_for_sale_category(self, *, sale_pricing_category, base_quantity):
        if sale_pricing_category == 'technician':
            return self.technician_price if self.technician_price is not None else self.standard_price
        if (sale_pricing_category == 'wholesale' and self.product.allow_wholesale
                and self.wholesale_price is not None and Decimal(base_quantity) >= self.product.wholesale_min_quantity):
            return self.wholesale_price
        if sale_pricing_category == 'retail':
            return self.retail_price if self.retail_price is not None else self.standard_price
        return self.standard_price
