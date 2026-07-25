from django import forms
from django.utils.text import slugify

from internetservices.tailwind import apply_tailwind
from custom_fields.forms import CustomFieldFormMixin

from .models import Product, ProductCategory
from customers.models import Customer


class ProductForm(CustomFieldFormMixin, forms.ModelForm):
    custom_field_target_model = "product"

    class Meta:
        model = Product
        fields = [
            'sku',
            'name',
            'item_type',
            'catalog_category',
            'brand',
            'model_number',
            'measure_unit',
            'buying_price',
            'selling_price',
            'retail_price',
            'wholesale_price',
            'wholesale_min_quantity',
            'allow_wholesale',
            'customer',
            'track_stock',
            'is_serialized',
            'track_expiry',
            'tax_eligible',
            'reorder_threshold',
            'is_active',
            'description',
            'category',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Important specifications, warranty notes, or supplier details'}),
        }
        help_texts = {
            'measure_unit': 'Examples: Unit, Meter, Kg, Box.',
            'buying_price': 'Your acquisition cost. Used for margin guidance.',
            'selling_price': 'Default selling price when no retail price is set.',
            'retail_price': 'Customer-facing standard price. Leave blank to use selling price.',
            'wholesale_price': 'Only used when wholesale pricing is enabled.',
            'wholesale_min_quantity': 'Minimum quantity required before wholesale price applies.',
            'customer': 'Optional. Link this product to a specific customer when it is assigned or reserved.',
            'is_active': 'Inactive products stay in history but are hidden from normal selling workflows.',
            'sku': 'Unique product or service code within this business.',
            'track_stock': 'Stock changes only through purchases and authorized adjustments.',
            'is_serialized': 'Each received unit must have a unique serial number.',
            'reorder_threshold': 'Low-stock alert threshold for this product.',
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super().__init__(*args, organization=self.organization, **kwargs)
        if self.organization is not None and 'customer' in self.fields:
            self.fields['customer'].queryset = Customer.objects.filter(organization=self.organization)
            self.fields['catalog_category'].queryset = ProductCategory.objects.filter(
                organization=self.organization, is_active=True
            )
        self.fields['sku'].required = False
        self.fields['item_type'].required = False
        self.fields['reorder_threshold'].required = False
        self.fields['customer'].empty_label = 'No customer association'
        self.fields['category'].empty_label = None
        self.fields['name'].widget.attrs.setdefault('placeholder', 'Router, radio, cable, software license...')
        self.fields['measure_unit'].widget.attrs.setdefault('placeholder', 'Unit')
        self.has_movement_history = bool(
            self.instance.pk
            and self.instance.stock_movements.exists()
        )
        if self.has_movement_history:
            for field_name in ('item_type', 'track_stock', 'is_serialized'):
                self.fields[field_name].disabled = True
                self.fields[field_name].help_text = (
                    'Locked because this item already has inventory or sales history.'
                )
        apply_tailwind(self)

    def clean_customer(self):
        customer = self.cleaned_data.get('customer')
        if customer and self.organization and customer.organization_id != self.organization.id:
            raise forms.ValidationError("Invalid customer for the active organization.")
        return customer

    def clean_sku(self):
        sku = (self.cleaned_data.get('sku') or '').strip().upper()
        if not sku:
            base = (slugify(self.cleaned_data.get('name') or 'ITEM').replace('-', '')[:24] or 'ITEM').upper()
            sku = base
            suffix = 1
            while Product.objects.unscoped().filter(tenant=self.organization, sku__iexact=sku).exclude(pk=self.instance.pk).exists():
                suffix += 1
                sku = f'{base}-{suffix}'
        if self.organization is not None:
            queryset = Product.objects.unscoped().filter(tenant=self.organization, sku__iexact=sku)
            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise forms.ValidationError('This SKU is already used in the active business.')
        return sku

    def clean(self):
        cleaned = super().clean()
        cleaned['item_type'] = cleaned.get('item_type') or Product.ItemType.PHYSICAL
        cleaned['reorder_threshold'] = cleaned.get('reorder_threshold') or 0
        if 'item_type' not in self.data and cleaned['item_type'] == Product.ItemType.PHYSICAL:
            cleaned['track_stock'] = True
        if cleaned.get('item_type') == Product.ItemType.SERVICE:
            cleaned['track_stock'] = False
            cleaned['is_serialized'] = False
            cleaned['track_expiry'] = False
        if cleaned.get('is_serialized'):
            cleaned['track_stock'] = True
        return cleaned
