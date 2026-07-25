from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.contrib.humanize.templatetags.humanize import intcomma
from users.tenant_models import TenantScopedManager

# Create your models here.

class ProductCategory(models.Model):
    organization = models.ForeignKey(
        'users.Organization', on_delete=models.PROTECT, related_name='product_categories', db_index=True
    )
    tenant = models.ForeignKey(
        'users.Organization', on_delete=models.PROTECT, related_name='tenant_product_categories', db_index=True
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
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
        super().save(*args, **kwargs)


class Product(models.Model):
    CATEGORY_CHOICES = (
        ('hardware', 'Hardware'),
        ('software', 'Software'),
        ('accessory', 'Accessory'),
        ('other', 'Other'),
    )

    class PricingMode(models.TextChoices):
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
        null=True,
        blank=True,
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
    buying_price = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    retail_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wholesale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    wholesale_min_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    allow_wholesale = models.BooleanField(default=False)
    customer = models.ForeignKey('customers.Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
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
        return self.measure_unit if self.measure_unit else 'Kg'
    
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
    
    def get_customer_display(self):
        return self.customer.name if self.customer else 'No Customer'
    
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
            'customer': self.get_customer_display(),
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
        if self.buying_price is not None and self.buying_price < 0:
            raise ValidationError({'buying_price': 'Buying price cannot be negative.'})
        if self.selling_price is not None and self.selling_price < 0:
            raise ValidationError({'selling_price': 'Selling price cannot be negative.'})
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

