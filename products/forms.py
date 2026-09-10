from decimal import Decimal

from django import forms
from django.utils.text import slugify

from internetservices.tailwind import apply_tailwind
from custom_fields.forms import CustomFieldFormMixin

from .models import Product, ProductCategory, UnitOfMeasure
from .images import prepare_product_image


class ProductForm(CustomFieldFormMixin, forms.ModelForm):
    custom_field_target_model = "product"

    COST_DIRECT = 'direct'
    COST_PACK = 'pack'
    acquisition_cost_mode = forms.ChoiceField(
        choices=((COST_DIRECT, 'Cost per base unit'), (COST_PACK, 'Cost per purchase pack')),
        widget=forms.RadioSelect,
        initial=COST_DIRECT,
        label='How do you enter acquisition cost?', required=False,
    )

    class Meta:
        model = Product
        fields = [
            'sku',
            'name',
            'image',
            'item_type',
            'catalog_category',
            'sales_unit',
            'brand',
            'model_number',
            'buying_price',
            'default_purchase_unit_label',
            'default_purchase_conversion_factor',
            'default_purchase_unit_cost',
            'selling_price',
            'technician_price',
            'wholesale_price',
            'wholesale_min_quantity',
            'allow_wholesale',
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
            'image': forms.ClearableFileInput(attrs={
                'accept': 'image/jpeg,image/png,image/webp',
                'data-product-image-input': '',
            }),
        }
        help_texts = {
            'buying_price': 'Your acquisition cost. Used for margin guidance.',
            'selling_price': 'Default price for ordinary and direct customers.',
            'technician_price': 'Special selling price for walk-in technicians. Leave blank to use Selling Price.',
            'wholesale_price': 'Only used when wholesale pricing is enabled.',
            'wholesale_min_quantity': 'Minimum quantity required before wholesale price applies.',
            'is_active': 'Inactive products stay in history but are hidden from normal selling workflows.',
            'sku': 'Unique product or service code within this business.',
            'image': 'JPEG, PNG, or WebP. The image is securely renamed and optimized automatically.',
            'track_stock': 'Stock changes only through purchases and authorized adjustments.',
            'is_serialized': 'Each received unit must have a unique serial number.',
            'reorder_threshold': 'Low-stock alert threshold for this product.',
            'tax_eligible': 'Include this product in VAT/tax when the sale has a tax rate. Untick only for exempt items.',
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super().__init__(*args, organization=self.organization, **kwargs)
        if self.organization is not None:
            self.fields['catalog_category'].queryset = ProductCategory.objects.filter(
                organization=self.organization, is_active=True
            )
            self.fields['sales_unit'].queryset = UnitOfMeasure.objects.filter(
                tenant=self.organization, is_active=True
            ).order_by('name')
        self.fields['sku'].required = False
        self.fields['item_type'].required = False
        self.fields['reorder_threshold'].required = False
        self.fields['buying_price'].required = False
        self.fields['default_purchase_conversion_factor'].required = False
        self.fields['category'].empty_label = None
        self.fields['default_purchase_unit_label'].label = 'Purchase unit label'
        self.fields['default_purchase_conversion_factor'].label = 'Units in one purchase pack'
        self.fields['default_purchase_unit_cost'].label = 'Default cost per purchase pack'
        self.fields['buying_price'].label = 'Cost per base stock unit'
        self.fields['buying_price'].widget.attrs.update({'step': '0.000001', 'min': '0'})
        self.fields['default_purchase_conversion_factor'].widget.attrs.update({'step': '0.000001', 'min': '0.000001'})
        self.fields['default_purchase_unit_cost'].widget.attrs.update({'step': '0.01', 'min': '0'})
        if self.instance.pk and self.instance.default_purchase_conversion_factor != Decimal('1.000000'):
            self.initial['acquisition_cost_mode'] = self.COST_PACK
        self.fields['name'].widget.attrs.setdefault('placeholder', 'Router, radio, cable, software license...')
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

    def clean_image(self):
        """Validate and optimize only newly uploaded product photos."""
        image = self.cleaned_data.get('image')
        uploaded_image = self.files.get('image')
        if not uploaded_image:
            return image
        return prepare_product_image(uploaded_image)

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
        catalog_category = cleaned.get('catalog_category')
        sales_unit = cleaned.get('sales_unit')
        if catalog_category:
            allowed_units = catalog_category.allowed_units.all()
            if sales_unit is None:
                sales_unit = catalog_category.default_unit
                cleaned['sales_unit'] = sales_unit
                self.instance.sales_unit = sales_unit
            if sales_unit is None or not allowed_units.filter(pk=sales_unit.pk).exists():
                self.add_error('sales_unit', 'Select a unit allowed by the chosen category.')
            elif sales_unit.tenant_id != catalog_category.tenant_id:
                self.add_error('sales_unit', 'Unit and category must belong to the same tenant.')
            else:
                self.instance.measure_unit = sales_unit.label
        elif cleaned.get('item_type') == Product.ItemType.PHYSICAL and cleaned.get('track_stock') and sales_unit is None:
            # Keep legacy form/API submissions working while callers migrate from
            # the old free-text ``measure_unit`` field. Product.save() resolves
            # this value to a tenant-scoped UnitOfMeasure only after the complete
            # form is valid, avoiding database writes during validation.
            legacy_measure_unit = (self.data.get('measure_unit') or '').strip()
            if legacy_measure_unit:
                self.instance.measure_unit = legacy_measure_unit
            else:
                self.add_error('sales_unit', 'Stockable products require a sales unit.')
        mode = cleaned.get('acquisition_cost_mode') or self.COST_DIRECT
        base_label = sales_unit.label if sales_unit else (self.instance.measure_unit or 'Unit')
        if mode == self.COST_PACK:
            factor = cleaned.get('default_purchase_conversion_factor')
            pack_cost = cleaned.get('default_purchase_unit_cost')
            label = (cleaned.get('default_purchase_unit_label') or '').strip()
            if not label:
                self.add_error('default_purchase_unit_label', 'Enter the purchase unit label, for example Box.')
            if factor is None or factor <= 0:
                self.add_error('default_purchase_conversion_factor', 'Units in a purchase pack must be greater than zero.')
            if pack_cost is None or pack_cost < 0:
                self.add_error('default_purchase_unit_cost', 'Purchase pack cost cannot be negative.')
            if factor and factor > 0 and pack_cost is not None and pack_cost >= 0:
                normalized = (pack_cost / factor).quantize(Decimal('0.000001'))
                cleaned['buying_price'] = normalized
                self.instance.buying_price = normalized
        else:
            buying = cleaned.get('buying_price')
            if buying is None:
                self.add_error('buying_price', 'Enter the acquisition cost per base stock unit.')
            cleaned['default_purchase_unit_label'] = base_label
            cleaned['default_purchase_conversion_factor'] = Decimal('1.000000')
            cleaned['default_purchase_unit_cost'] = buying
            self.instance.default_purchase_unit_label = base_label
            self.instance.default_purchase_conversion_factor = Decimal('1.000000')
            self.instance.default_purchase_unit_cost = buying

        for field_name, label in (
            ('selling_price', 'Selling price'),
            ('technician_price', 'Technician price'),
            ('wholesale_price', 'Wholesale price'),
        ):
            value = cleaned.get(field_name)
            floor = cleaned.get('buying_price')
            if self.instance.pk:
                from .pricing import cost_floor_for
                floor = cost_floor_for(self.instance)
            if value is not None and floor is not None and value <= floor:
                self.add_error(field_name, f'{label} must be greater than normalized base-unit cost.')
        return cleaned
