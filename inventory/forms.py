from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from customers.models import Customer
from internetservices.tailwind import apply_tailwind
from products.models import Product, ProductCategory

from .models import Cart, CartLine, InventorySettings, Purchase, PurchaseLine, StockAdjustment, StockUnit, Supplier, SupplierPaymentRecord


class TenantFormMixin:
    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class ProductCategoryForm(TenantFormMixin, forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ['name', 'description', 'is_active']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        query = ProductCategory.objects.unscoped().filter(tenant=self.organization, name__iexact=name)
        if self.instance.pk:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise forms.ValidationError('A category with this name already exists.')
        return name


class SupplierForm(TenantFormMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['company_name', 'contact_person', 'phone', 'email', 'physical_address', 'tin_vrn', 'notes', 'is_active']
        widgets = {'physical_address': forms.Textarea(attrs={'rows': 2}), 'notes': forms.Textarea(attrs={'rows': 3})}
        help_texts = {
            'phone': 'Include the country code where possible, for example +255.',
            'tin_vrn': 'Optional tax identification or VAT registration number.',
            'is_active': 'Inactive suppliers remain in purchase history but cannot be selected for new purchases.',
        }

    def clean_company_name(self):
        value = (self.cleaned_data.get('company_name') or '').strip()
        query = Supplier.objects.unscoped().filter(tenant=self.organization, company_name__iexact=value)
        if self.instance.pk:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise forms.ValidationError('This supplier already exists.')
        return value


class SupplierPaymentForm(TenantFormMixin, forms.ModelForm):
    class Meta:
        model = SupplierPaymentRecord
        fields = ['payment_date', 'amount', 'method', 'reference', 'notes']
        widgets = {'payment_date': forms.DateInput(attrs={'type': 'date'}), 'notes': forms.Textarea(attrs={'rows': 3})}

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is None or amount <= 0:
            raise forms.ValidationError('Payment amount must be greater than zero.')
        return amount


class PurchaseForm(TenantFormMixin, forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ['supplier', 'reference_number', 'purchase_date', 'notes']
        widgets = {'purchase_date': forms.DateInput(attrs={'type': 'date'}), 'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.filter(tenant=organization, is_active=True)

    def clean_reference_number(self):
        value = (self.cleaned_data.get('reference_number') or '').strip()
        query = Purchase.objects.unscoped().filter(tenant=self.organization, reference_number__iexact=value)
        if self.instance.pk:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise forms.ValidationError('This purchase reference already exists.')
        return value


class PurchaseLineForm(TenantFormMixin, forms.ModelForm):
    class Meta:
        model = PurchaseLine
        fields = ['product', 'quantity', 'unit_cost', 'batch_reference', 'expiry_date', 'serial_numbers']
        widgets = {
            'quantity': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
            'unit_cost': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'serial_numbers': forms.Textarea(attrs={'rows': 3, 'placeholder': 'One serial per line'}),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(
            tenant=organization, is_active=True, item_type=Product.ItemType.PHYSICAL, track_stock=True
        )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get('product')
        quantity = cleaned.get('quantity') or Decimal('0.00')
        serials = [
            value.strip()
            for value in (cleaned.get('serial_numbers') or '').replace(',', '\n').splitlines()
            if value.strip()
        ]
        if product and product.is_serialized:
            if quantity != quantity.to_integral_value():
                self.add_error('quantity', 'Serialized products require a whole-number quantity.')
            elif len(serials) != int(quantity):
                self.add_error('serial_numbers', f'Enter exactly {int(quantity)} unique serial numbers.')
            if len(serials) != len(set(value.upper() for value in serials)):
                self.add_error('serial_numbers', 'Serial numbers must be unique within this line.')
        return cleaned


class PurchaseLineFormSet(BaseInlineFormSet):
    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.organization = organization
            form.fields['product'].queryset = Product.objects.filter(
                tenant=organization, is_active=True, item_type=Product.ItemType.PHYSICAL, track_stock=True
            )

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs['organization'] = self.organization
        return kwargs

    def clean(self):
        super().clean()
        active = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get('DELETE')]
        if not active:
            raise forms.ValidationError('Add at least one purchase line.')


PurchaseLinesFormSet = inlineformset_factory(
    Purchase,
    PurchaseLine,
    form=PurchaseLineForm,
    formset=PurchaseLineFormSet,
    extra=1,
    can_delete=True,
)


class StockAdjustmentForm(TenantFormMixin, forms.Form):
    DIRECTION_INCREASE = 'increase'
    DIRECTION_DECREASE = 'decrease'

    product = forms.ModelChoiceField(queryset=Product.objects.none())
    direction = forms.ChoiceField(
        choices=((DIRECTION_INCREASE, 'Increase stock'), (DIRECTION_DECREASE, 'Decrease stock')),
        widget=forms.RadioSelect,
    )
    quantity = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        help_text='Enter the number of units to add or remove.',
        widget=forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
    )
    reason = forms.ChoiceField(choices=StockAdjustment.Reason.choices)
    serial_numbers = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 4}), help_text='One serial per line for serialized items.')
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(
            tenant=organization, item_type=Product.ItemType.PHYSICAL, track_stock=True, is_active=True
        ).order_by('name')

    def clean(self):
        cleaned = super().clean()
        quantity = cleaned.get('quantity')
        direction = cleaned.get('direction')
        if quantity is not None:
            cleaned['quantity_delta'] = -quantity if direction == self.DIRECTION_DECREASE else quantity
        return cleaned

    def serial_list(self):
        return [value.strip() for value in (self.cleaned_data.get('serial_numbers') or '').replace(',', '\n').splitlines() if value.strip()]


class CartForm(TenantFormMixin, forms.ModelForm):
    class Meta:
        model = Cart
        fields = ['customer', 'walk_in_name', 'discount_amount', 'tax_rate', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}
        help_texts = {
            'customer': 'Leave blank for a walk-in customer.',
            'walk_in_name': 'Optional name to print when no customer record is selected.',
            'discount_amount': 'Cart-wide discount applied after line discounts.',
            'tax_rate': 'VAT percentage applied after discounts.',
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['customer'].queryset = Customer.objects.filter(tenant=organization, status=Customer.Status.ACTIVE)
        self.fields['customer'].required = False

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get('customer')
        if customer and customer.tenant_id != self.organization.id:
            raise forms.ValidationError('Customer must belong to the active tenant.')
        if (cleaned.get('discount_amount') or 0) < 0:
            self.add_error('discount_amount', 'Discount cannot be negative.')
        if (cleaned.get('tax_rate') or 0) < 0:
            self.add_error('tax_rate', 'VAT/tax rate cannot be negative.')
        return cleaned


class CartLineForm(TenantFormMixin, forms.ModelForm):
    serial_units = forms.ModelMultipleChoiceField(queryset=StockUnit.objects.none(), required=False)

    class Meta:
        model = CartLine
        fields = ['product', 'quantity', 'discount_amount']

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(tenant=organization, is_active=True).order_by('name')
        product_id = self.data.get('product') if self.is_bound else self.instance.product_id
        if product_id:
            self.fields['serial_units'].queryset = StockUnit.objects.filter(
                tenant=organization, product_id=product_id, status=StockUnit.Status.AVAILABLE
            )
        if self.instance.pk:
            self.fields['serial_units'].initial = self.instance.serial_selections.values_list('stock_unit_id', flat=True)

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get('product')
        quantity = cleaned.get('quantity') or Decimal('0.00')
        discount = cleaned.get('discount_amount') or Decimal('0.00')
        if quantity <= 0:
            self.add_error('quantity', 'Quantity must be greater than zero.')
        if product and product.is_serialized:
            if quantity != quantity.to_integral_value():
                self.add_error('quantity', 'Serialized items require a whole quantity.')
            if len(cleaned.get('serial_units') or []) != int(quantity):
                self.add_error('serial_units', 'Select one available serial for every unit.')
        if product and product.item_type == Product.ItemType.PHYSICAL and product.track_stock:
            available = product.available_stock
            if available <= 0:
                self.add_error('product', 'This product is currently out of stock.')
            elif quantity > available:
                self.add_error('quantity', f'Only {available} units are currently available.')
        if product and discount > product.price_for(quantity=quantity) * quantity:
            self.add_error('discount_amount', 'Discount cannot exceed the line amount.')
        return cleaned


class InventoryImportForm(TenantFormMixin, forms.Form):
    import_type = forms.ChoiceField(choices=(
        ('products', 'Products'),
        ('suppliers', 'Suppliers'),
        ('opening_stock', 'Opening stock balances'),
        ('historical_purchases', 'Historical purchases (record only)'),
        ('historical_sales', 'Historical sales (record only)'),
    ))
    workbook = forms.FileField(help_text='Upload an .xlsx workbook using the downloadable template.')


class InventorySettingsForm(TenantFormMixin, forms.ModelForm):
    class Meta:
        model = InventorySettings
        fields = [
            'walk_in_customer_label', 'dead_stock_days', 'fast_moving_days', 'fast_moving_min_units',
            'slow_moving_days', 'slow_moving_max_units',
        ]
