from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.urls import reverse
from django.contrib.humanize.templatetags.humanize import intcomma
from users.tenant_models import TenantScopedManager

# Create your models here.

class UnitOfMeasure(models.Model):
    """Tenant-owned sales unit used by product categories and products."""

    organization = models.ForeignKey(
        'users.Organization', on_delete=models.PROTECT, related_name='units_of_measure', db_index=True
    )
    tenant = models.ForeignKey(
        'users.Organization', on_delete=models.PROTECT, related_name='tenant_units_of_measure', db_index=True
    )
    name = models.CharField(max_length=50)
    symbol = models.CharField(max_length=16, blank=True, default='')
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name'], name='uniq_uom_tenant_name'),
        ]
        indexes = [models.Index(fields=['tenant', 'is_active', 'name'])]

    def __str__(self):
        return f'{self.name} ({self.symbol})' if self.symbol else self.name

    @property
    def label(self):
        return self.symbol or self.name

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        self.name = (self.name or '').strip()
        self.symbol = (self.symbol or '').strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if not (self.name or '').strip():
            raise ValidationError({'name': 'Unit name is required.'})
        if self.tenant_id and type(self).objects.unscoped().filter(
            tenant_id=self.tenant_id, name__iexact=(self.name or '').strip()
        ).exclude(pk=self.pk).exists():
            raise ValidationError({'name': 'A unit with this name already exists for the tenant.'})


class ProductCategory(models.Model):
    class Icon(models.TextChoices):
        GENERIC = 'layers', 'General catalog'
        CAMERA = 'camera', 'Camera & security'
        TOOLS = 'tools', 'Installation & tools'
        LAPTOP = 'laptop', 'Computing'
        ROUTER = 'router', 'Networking'
        SWITCH = 'switch', 'Network switching'
        CABLE = 'cable', 'Cables & accessories'

    organization = models.ForeignKey(
        'users.Organization', on_delete=models.PROTECT, related_name='product_categories', db_index=True
    )
    tenant = models.ForeignKey(
        'users.Organization', on_delete=models.PROTECT, related_name='tenant_product_categories', db_index=True
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    measure_unit = models.CharField(
        max_length=50,
        default='Unit',
        help_text='Default unit for new products assigned to this category, for example Unit, Pc, Meter, Kg, or Box.',
    )
    allowed_units = models.ManyToManyField(
        UnitOfMeasure,
        related_name='product_categories',
        blank=True,
        help_text='Units that products in this category may use for sales.',
    )
    default_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name='default_for_product_categories',
        null=True,
        blank=True,
        help_text='Preselected sales unit for new products in this category.',
    )
    icon = models.CharField(max_length=20, choices=Icon.choices, default=Icon.GENERIC)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'name'], name='uniq_product_category_tenant_name'),
        ]
        indexes = [models.Index(fields=['tenant', 'is_active', 'name'])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        self.name = self.name.strip()
        if self.default_unit_id:
            self.measure_unit = self.default_unit.label
        else:
            self.measure_unit = (self.measure_unit or 'Unit').strip()
        self.full_clean(exclude=['allowed_units', 'default_unit'])
        super().save(*args, **kwargs)
        if not self.default_unit_id:
            unit, _ = UnitOfMeasure.objects.unscoped().get_or_create(
                tenant_id=self.tenant_id,
                name__iexact=self.measure_unit,
                defaults={'organization_id': self.tenant_id, 'name': self.measure_unit},
            )
            type(self).objects.unscoped().filter(pk=self.pk).update(default_unit=unit)
            self.default_unit = unit
        if not self.allowed_units.filter(pk=self.default_unit_id).exists():
            self.allowed_units.add(self.default_unit)

    def clean(self):
        super().clean()
        if self.default_unit_id and self.tenant_id and self.default_unit.tenant_id != self.tenant_id:
            raise ValidationError({'default_unit': 'Default unit must belong to the active tenant.'})
        if self.pk and self.default_unit_id and not self.allowed_units.filter(pk=self.default_unit_id).exists():
            raise ValidationError({'default_unit': 'Default unit must be one of the category allowed units.'})


@receiver(m2m_changed, sender=ProductCategory.allowed_units.through)
def protect_category_unit_tenant_boundary(sender, instance, action, reverse, pk_set, **kwargs):
    """Reject cross-tenant category/unit links from every ORM write path."""
    if action != 'pre_add' or not pk_set:
        return
    if reverse:
        has_foreign_link = ProductCategory.objects.unscoped().filter(pk__in=pk_set).exclude(
            tenant_id=instance.tenant_id,
        ).exists()
    else:
        has_foreign_link = UnitOfMeasure.objects.unscoped().filter(pk__in=pk_set).exclude(
            tenant_id=instance.tenant_id,
        ).exists()
    if has_foreign_link:
        raise ValidationError('Allowed units and product categories must belong to the same tenant.')


class Product(models.Model):
    CATEGORY_CHOICES = (
        ('hardware', 'Hardware'),
        ('software', 'Software'),
        ('accessory', 'Accessory'),
        ('other', 'Other'),
    )

    class PricingMode(models.TextChoices):
        STANDARD = "standard", "Standard"
        TECHNICIAN = "technician", "Technician"
        RETAIL = "retail", "Retail"
        WHOLESALE = "wholesale", "Wholesale"

    class ItemType(models.TextChoices):
        PHYSICAL = "physical", "Physical product"
        SERVICE = "service", "Service"

    def _format_price(self, price):
        """Helper method to format prices with commas"""
        # Convert Decimal to float for consistent formatting
        price_float = float(price)
        # Format with commas and remove .00 if present
        return f"{intcomma(round(price_float, 2))}".replace('.00', '')
    
    def get_buying_price_display(self):
        formatted = self._format_price(self.buying_price)
        return f"Tshs {formatted}"
    
    def get_selling_price_display(self):    
        formatted = self._format_price(self.selling_price)
        return f"Tshs {formatted}"

    def get_profit(self):
        profit = self.selling_price - self.buying_price
        formatted_profit = self._format_price(profit)
        return f"Tshs {formatted_profit}"

    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='products',
        null=True,
        blank=True,
        db_index=True,
    )
    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_products',
        db_index=True,
    )
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=64, blank=True, default='', db_index=True)
    description = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    catalog_category = models.ForeignKey(
        ProductCategory,
        on_delete=models.PROTECT,
        related_name='products',
        null=True,
        blank=True,
    )
    item_type = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.PHYSICAL, db_index=True)
    brand = models.CharField(max_length=120, blank=True, default='')
    model_number = models.CharField(max_length=120, blank=True, default='')
    track_stock = models.BooleanField(default=True, db_index=True)
    is_serialized = models.BooleanField(default=False, db_index=True)
    track_expiry = models.BooleanField(default=False)
    tax_eligible = models.BooleanField(default=True)
    reorder_threshold = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    measure_unit = models.CharField(max_length=50, default='Kg')
    sales_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name='products',
        null=True,
        blank=True,
        help_text='Unit shown on quotations and invoices for this product.',
    )
    buying_price = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    retail_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    technician_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wholesale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wholesale_min_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    allow_wholesale = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    stock = models.PositiveIntegerField(default=0)  # <-- Add this line
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'sku'],
                condition=~Q(sku=''),
                name='uniq_product_sku_per_tenant',
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "category", "is_active"], name="products_org_cat_active_idx"),
            models.Index(fields=["tenant", "category", "is_active"], name="products_ten_cat_active_idx"),
            models.Index(fields=["organization", "name"], name="products_org_name_idx"),
            models.Index(fields=["organization", "quantity"], name="products_org_quantity_idx"),
            models.Index(fields=["organization", "retail_price"], name="products_org_retail_idx"),
            models.Index(fields=["organization", "technician_price"], name="products_org_tech_idx"),
            models.Index(fields=["organization", "wholesale_price"], name="products_org_wholesale_idx"),
            models.Index(fields=["tenant", "item_type", "is_active"], name="products_ten_type_active_idx"),
            models.Index(fields=["tenant", "sku"], name="products_ten_sku_idx"),
        ]
    
    def __str__(self):
        return self.name
    
    def get_profit(self):
        return self.selling_price - self.buying_price
    
    def get_absolute_url(self):
        return reverse('product-detail', kwargs={'pk': self.pk})
    
    def get_update_url(self):
        return reverse('product-update', kwargs={'pk': self.pk})
    
    def get_delete_url(self):
        return reverse('product-delete', kwargs={'pk': self.pk})
    
    def get_create_url(self):
        return reverse('product-create')
    
    def get_list_url(self):
        return reverse('product-list')
    
    def get_category_display(self):
        return dict(self.CATEGORY_CHOICES).get(self.category, 'Unknown')
    
    def get_measure_unit_display(self):
        if self.sales_unit_id:
            return self.sales_unit.label
        return self.measure_unit if self.measure_unit else 'Unit'
    
    def get_buying_price_display(self):
        return f"Tshs{self.buying_price:.2f}"  
    
    def get_selling_price_display(self):    
        return f"Tshs{self.selling_price:.2f}"

    def get_quantity_display(self):
        return f"{self.quantity} {self.get_measure_unit_display()}" 
    
    def get_created_at_display(self):
        return self.created_at.strftime('%Y-%m-%d %H:%M:%S')
    
    def get_updated_at_display(self):
        return self.updated_at.strftime('%Y-%m-%d %H:%M:%S')    
    
    def get_is_active_display(self):
        return 'Active' if self.is_active else 'Inactive'
    
    def get_product_details(self):
        return {
            'name': self.name,
            'description': self.description,
            'category': self.get_category_display(),
            'quantity': self.get_quantity_display(),
            'measure_unit': self.get_measure_unit_display(),
            'buying_price': self.get_buying_price_display(),
            'selling_price': self.get_selling_price_display(),
            'created_at': self.get_created_at_display(),
            'updated_at': self.get_updated_at_display(),
            'is_active': self.get_is_active_display()
        }
    
    def get_product_summary(self):
        return {
            'name': self.name,
            'category': self.get_category_display(),
            'quantity': self.get_quantity_display(),
            'buying_price': self.get_buying_price_display(),
            'selling_price': self.get_selling_price_display(),
            'profit': self.get_profit(),  # Now returns formatted string
            'is_active': self.get_is_active_display()
        }
    
    def get_product_summary_list(self):
        return {
            'name': self.name,
            'category': self.get_category_display(),
            'quantity': self.get_quantity_display(),
            'buying_price': self.get_buying_price_display(),
            'selling_price': self.get_selling_price_display(),
            'profit': f"Tshs{self.get_profit():.2f}",
            'is_active': self.get_is_active_display()
        }
    
    def get_product_summary_dict(self):
        return {
            'name': self.name,
            'category': self.get_category_display(),
            'quantity': self.get_quantity_display(),
            'buying_price': self.get_buying_price_display(),
            'selling_price': self.get_selling_price_display(),
            'profit': f"Tshs{self.get_profit():.2f}",
            'is_active': self.get_is_active_display()
        }

    @property
    def effective_technician_price(self):
        return self.technician_price if self.technician_price is not None else self.selling_price

    def price_for_sale_category(self, *, sale_pricing_category: str, quantity=1):
        """Resolve a future transaction price from one centralized catalog rule.

        ``retail`` is retained only for pre-existing workflows and draft carts.
        New transactions explicitly select standard, technician, or wholesale.
        """
        if sale_pricing_category == self.PricingMode.TECHNICIAN:
            return self.effective_technician_price
        if sale_pricing_category == self.PricingMode.WHOLESALE:
            if self.allow_wholesale and self.wholesale_price is not None and quantity >= self.wholesale_min_quantity:
                return self.wholesale_price
            return self.selling_price
        if sale_pricing_category == self.PricingMode.STANDARD:
            return self.selling_price
        return self.price_for(quantity=quantity, pricing_mode=self.PricingMode.RETAIL)

    def price_for(self, *, quantity=1, pricing_mode: str = PricingMode.RETAIL):
        if (
            pricing_mode == self.PricingMode.WHOLESALE
            and self.allow_wholesale
            and self.wholesale_price is not None
            and quantity >= self.wholesale_min_quantity
        ):
            return self.wholesale_price
        return self.retail_price if self.retail_price is not None else self.selling_price

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        if self.catalog_category_id:
            previous_category_id = None
            if not self._state.adding and self.pk:
                previous_category_id = type(self).objects.unscoped().filter(pk=self.pk).values_list(
                    'catalog_category_id', flat=True
                ).first()
            if self._state.adding or previous_category_id != self.catalog_category_id:
                selected_is_allowed = self.sales_unit_id and self.catalog_category.allowed_units.filter(
                    pk=self.sales_unit_id
                ).exists()
                if self.catalog_category.default_unit_id and not selected_is_allowed:
                    self.sales_unit = self.catalog_category.default_unit
                self.measure_unit = self.catalog_category.measure_unit
        if not self.sales_unit_id and self.tenant_id:
            legacy_name = (self.measure_unit or 'Unit').strip() or 'Unit'
            self.sales_unit, _ = UnitOfMeasure.objects.unscoped().get_or_create(
                tenant_id=self.tenant_id,
                name__iexact=legacy_name,
                defaults={'organization_id': self.tenant_id, 'name': legacy_name},
            )
        if self.sales_unit_id:
            self.measure_unit = self.sales_unit.label
        self.sku = (self.sku or '').strip().upper()
        if self.item_type == self.ItemType.SERVICE:
            self.track_stock = False
            self.is_serialized = False
            self.track_expiry = False
        if self.is_serialized:
            self.track_stock = True
        self.full_clean(exclude=['quantity', 'stock'])
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.catalog_category_id and self.tenant_id:
            category_tenant_id = getattr(self.catalog_category, 'tenant_id', None)
            if category_tenant_id != self.tenant_id:
                raise ValidationError({'catalog_category': 'Category must belong to the active tenant.'})
        if self.sales_unit_id and self.tenant_id and self.sales_unit.tenant_id != self.tenant_id:
            raise ValidationError({'sales_unit': 'Unit must belong to the active tenant.'})
        if self.catalog_category_id and self.sales_unit_id and self.catalog_category.pk:
            if not self.catalog_category.allowed_units.filter(pk=self.sales_unit_id).exists():
                raise ValidationError({'sales_unit': 'Select a unit allowed by the product category.'})
        if self.buying_price is not None and self.buying_price < 0:
            raise ValidationError({'buying_price': 'Buying price cannot be negative.'})
        if self.selling_price is not None and self.selling_price < 0:
            raise ValidationError({'selling_price': 'Selling price cannot be negative.'})
        if self.selling_price is not None and self.buying_price is not None and self.selling_price <= self.buying_price:
            raise ValidationError({'selling_price': 'Selling price must be greater than buying cost.'})
        for field_name, label in (
            ('retail_price', 'Retail price'),
            ('wholesale_price', 'Wholesale price'),
            ('technician_price', 'Technician price'),
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValidationError({field_name: f'{label} cannot be negative.'})
        for field_name, label in (
            ('wholesale_price', 'Wholesale price'),
            ('technician_price', 'Technician price'),
        ):
            value = getattr(self, field_name)
            if value is not None and self.buying_price is not None and value <= self.buying_price:
                raise ValidationError({field_name: f'{label} must be greater than buying cost.'})
        if self.reorder_threshold is not None and self.reorder_threshold < 0:
            raise ValidationError({'reorder_threshold': 'Reorder threshold cannot be negative.'})

    @property
    def available_stock(self):
        if not self.track_stock or self.item_type == self.ItemType.SERVICE:
            return Decimal('0.00')
        try:
            return self.inventory_balance.quantity
        except Exception:
            return Decimal(str(self.stock or 0))

    @property
    def profit_margin_percent(self):
        if not self.selling_price:
            return Decimal('0.00')
        return ((self.selling_price - self.buying_price) / self.selling_price * Decimal('100')).quantize(Decimal('0.01'))

