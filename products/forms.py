from decimal import Decimal
from uuid import uuid4

from django import forms
from django.utils.text import slugify

from internetservices.tailwind import apply_tailwind
from custom_fields.forms import CustomFieldFormMixin

from .models import Product, ProductCategory, UnitOfMeasure
from .images import prepare_product_image


class ProductForm(CustomFieldFormMixin, forms.ModelForm):
    custom_field_target_model = "product"

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
        self.actor = kwargs.pop('actor', None)
        super().__init__(*args, organization=self.organization, **kwargs)
        self.current_cost_floor = None
        self.movement_backed_cost = False
        if self.instance.pk:
            from .pricing import can_view_cost, cost_floor_details

            floor, self.movement_backed_cost = cost_floor_details(self.instance)
            if (
                self.organization is not None
                and self.instance.tenant_id == self.organization.pk
                and can_view_cost(actor=self.actor, organization=self.organization)
            ):
                self.current_cost_floor = floor
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
        self.fields['category'].empty_label = None
        unit_label = self.instance.get_measure_unit_display() if self.instance.pk else 'selected sales unit'
        self.fields['buying_price'].label = f'Buying price per {unit_label}'
        self.fields['selling_price'].label = f'Selling price per {unit_label}'
        self.fields['technician_price'].label = f'Technician price per {unit_label}'
        self.fields['wholesale_price'].label = f'Wholesale price per {unit_label}'
        self.fields['buying_price'].widget.attrs.update({'step': '0.000001', 'min': '0'})
        self.fields['name'].widget.attrs.setdefault('placeholder', 'Router, radio, cable, software license...')
        self.has_transaction_history = bool(self.instance.pk and self.instance.has_unit_history())
        # Compatibility name used by the existing template/view context.
        self.has_movement_history = self.has_transaction_history
        if self.has_transaction_history:
            for field_name in ('item_type', 'track_stock', 'is_serialized', 'sales_unit'):
                self.fields[field_name].disabled = True
                self.fields[field_name].help_text = (
                    'Locked because this item already has stock, purchasing, cart, or billing history.'
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
        floor = cleaned.get('buying_price')
        if self.instance.pk and self.movement_backed_cost:
            from .pricing import cost_floor_for

            floor = cost_floor_for(self.instance)
        elif self.instance.pk and self.current_cost_floor is not None:
            self.current_cost_floor = floor
        for field_name, label in (
            ('selling_price', 'Selling price'),
            ('technician_price', 'Technician price'),
            ('wholesale_price', 'Wholesale price'),
        ):
            value = cleaned.get(field_name)
            if value is not None and floor is not None and value <= floor:
                if self.current_cost_floor is not None:
                    message = (
                        f'{label} must be greater than the current inventory cost of '
                        f'TZS {floor:,.6f} per selected sales unit.'
                        if self.movement_backed_cost else
                        f'{label} must be greater than the buying cost of TZS {floor:,.6f} per selected sales unit.'
                    )
                else:
                    message = f'{label} must be greater than the cost per selected sales unit.'
                self.add_error(field_name, message)
        return cleaned


class ProductUnitSuccessorForm(forms.Form):
    """Create a new catalog identity when the business adopts a different stock unit."""

    name = forms.CharField(max_length=200, label='New product name')
    sku = forms.CharField(max_length=50, label='New SKU')
    catalog_category = forms.ModelChoiceField(queryset=ProductCategory.objects.none())
    sales_unit = forms.ModelChoiceField(queryset=UnitOfMeasure.objects.none(), label='New sales / stock unit')
    buying_price = forms.DecimalField(max_digits=16, decimal_places=6, min_value=Decimal('0'))
    selling_price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
    technician_price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'), required=False)
    allow_wholesale = forms.BooleanField(required=False)
    wholesale_price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'), required=False)
    wholesale_min_quantity = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.01'), initial=Decimal('1.00'),
    )
    reason = forms.CharField(
        min_length=10,
        max_length=500,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Explain why future purchases and sales need a different unit.',
    )
    acknowledge = forms.BooleanField(
        label='I understand stock is not converted or transferred automatically.',
    )
    transition_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, organization, source_product, **kwargs):
        self.organization = organization
        self.source_product = source_product
        initial = kwargs.setdefault('initial', {})
        initial.setdefault('name', source_product.name)
        initial.setdefault('sku', self._suggest_sku(source_product.sku))
        initial.setdefault('catalog_category', source_product.catalog_category_id)
        initial.setdefault('buying_price', source_product.buying_price)
        initial.setdefault('selling_price', source_product.selling_price)
        initial.setdefault('technician_price', source_product.technician_price)
        initial.setdefault('allow_wholesale', source_product.allow_wholesale)
        initial.setdefault('wholesale_price', source_product.wholesale_price)
        initial.setdefault('wholesale_min_quantity', source_product.wholesale_min_quantity)
        initial.setdefault('transition_key', uuid4())
        super().__init__(*args, **kwargs)
        self.fields['catalog_category'].queryset = ProductCategory.objects.filter(
            tenant=organization, is_active=True,
        ).prefetch_related('allowed_units').order_by('name')
        self.fields['sales_unit'].queryset = UnitOfMeasure.objects.filter(
            tenant=organization, is_active=True,
        ).order_by('name')
        apply_tailwind(self)

    @staticmethod
    def _suggest_sku(source_sku):
        base = (source_sku or 'ITEM').strip().upper()[:42]
        return f'{base}-NEW'

    def clean_sku(self):
        sku = (self.cleaned_data.get('sku') or '').strip().upper()
        if Product.objects.unscoped().filter(tenant=self.organization, sku__iexact=sku).exists():
            raise forms.ValidationError('This SKU is already used in the active business.')
        return sku

    def clean(self):
        cleaned = super().clean()
        unit = cleaned.get('sales_unit')
        category = cleaned.get('catalog_category')
        if unit and unit.tenant_id != self.organization.id:
            self.add_error('sales_unit', 'Select a unit belonging to the active business.')
        if category and category.tenant_id != self.organization.id:
            self.add_error('catalog_category', 'Select a category belonging to the active business.')
        if unit and self.source_product.sales_unit_id == unit.pk:
            self.add_error('sales_unit', 'Select a different unit. Ordinary price edits belong on the existing product.')
        if category and unit and not category.allowed_units.filter(pk=unit.pk, is_active=True).exists():
            self.add_error('sales_unit', 'Select an active unit allowed by the chosen category.')

        floor = cleaned.get('buying_price')
        for field_name in ('selling_price', 'technician_price'):
            value = cleaned.get(field_name)
            if value is not None and floor is not None and value <= floor:
                self.add_error(field_name, 'Selling prices must be greater than the new unit buying cost.')
        if cleaned.get('allow_wholesale'):
            wholesale = cleaned.get('wholesale_price')
            if wholesale is None:
                self.add_error('wholesale_price', 'Enter the wholesale price or disable wholesale pricing.')
            elif floor is not None and wholesale <= floor:
                self.add_error('wholesale_price', 'Wholesale price must be greater than the new unit buying cost.')
        else:
            cleaned['wholesale_price'] = None
        return cleaned
