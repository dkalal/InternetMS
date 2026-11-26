from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from customers.models import Customer
from internetservices.tailwind import apply_tailwind
from products.models import Product, ProductCategory, UnitOfMeasure
from users.permissions import (
    PermissionCode,
    discount_authorization_error,
    has_tenant_permission,
    maximum_discount_for,
    permission_grant_for,
)

from .models import Cart, CartLine, CartSerialSelection, InventorySettings, Purchase, PurchaseLine, StockAdjustment, StockUnit, Supplier, SupplierPaymentRecord


class CartPaymentForm(forms.Form):
    payment_method = forms.ChoiceField(choices=[
        ('', 'Select payment method'), ('cash', 'Cash'), ('bank', 'Bank'),
        ('mobile_money', 'Mobile money'), ('other', 'Other'),
    ])
    payment_reference = forms.CharField(max_length=80, required=False, label='Payment reference')

    def __init__(self, *args, cart_id, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)
        for field in self.fields.values():
            field.widget.attrs['form'] = f'cart-details-form-{cart_id}'
        # Draft saves and quotations do not require payment details.
        self.use_required_attribute = False


class TenantFormMixin:
    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class PurchaseProductChoiceField(forms.ModelChoiceField):
    """Tenant-scoped purchase choice with a useful, searchable display label."""

    def label_from_instance(self, product):
        return f'{product.name} · {product.sku}' if product.sku else product.name


class ProductCategoryForm(TenantFormMixin, forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ['name', 'description', 'allowed_units', 'default_unit', 'measure_unit', 'icon', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'What belongs in this category?'}),
            'allowed_units': forms.CheckboxSelectMultiple,
            'measure_unit': forms.HiddenInput,
            'icon': forms.RadioSelect,
        }
        help_texts = {
            'allowed_units': 'Products in this category may use any selected sales unit.',
            'default_unit': 'Applied automatically to new products assigned to this category.',
            'is_active': 'Inactive categories stay in history but cannot be used for new catalog items.',
        }

    def __init__(self, *args, organization=None, sale_pricing_category=Cart.SalePricingCategory.STANDARD, **kwargs):
        self.sale_pricing_category = sale_pricing_category
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['name'].widget.attrs.setdefault('placeholder', 'e.g. Network switches')
        unit_queryset = UnitOfMeasure.objects.filter(tenant=organization)
        if not self.instance.pk:
            unit_queryset = unit_queryset.filter(is_active=True)
        self.fields['allowed_units'].queryset = unit_queryset.order_by('name')
        self.fields['default_unit'].queryset = unit_queryset.order_by('name')
        self.fields['allowed_units'].required = False
        self.fields['default_unit'].required = False
        self.fields['measure_unit'].required = False
        self.fields['icon'].widget.attrs['class'] = 'jims-category-icon-input'

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        query = ProductCategory.objects.unscoped().filter(tenant=self.organization, name__iexact=name)
        if self.instance.pk:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise forms.ValidationError('A category with this name already exists.')
        return name

    def clean(self):
        cleaned = super().clean()
        allowed_units = cleaned.get('allowed_units')
        default_unit = cleaned.get('default_unit')
        legacy_unit = (cleaned.get('measure_unit') or '').strip()
        if default_unit and allowed_units is not None and default_unit not in allowed_units:
            self.add_error('default_unit', 'Default unit must be selected in Allowed units.')
        if allowed_units is not None and not allowed_units.exists() and not legacy_unit:
            self.add_error('allowed_units', 'Select at least one allowed unit.')
        if allowed_units is not None and allowed_units.exists() and not default_unit:
            self.add_error('default_unit', 'Select a default unit.')
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        legacy_unit = (self.cleaned_data.get('measure_unit') or '').strip()
        allowed_units = self.cleaned_data.get('allowed_units')
        default_unit = self.cleaned_data.get('default_unit')
        if not default_unit and legacy_unit and self.organization:
            default_unit, _ = UnitOfMeasure.objects.unscoped().get_or_create(
                tenant=self.organization,
                name__iexact=legacy_unit,
                defaults={'organization': self.organization, 'name': legacy_unit},
            )
            instance.default_unit = default_unit
            instance.measure_unit = default_unit.label
        if commit:
            instance.save()
            self.save_m2m()
            if allowed_units is not None and allowed_units.exists():
                instance.allowed_units.set(allowed_units)
            elif default_unit:
                instance.allowed_units.set([default_unit])
        return instance


class QuickProductCategoryForm(TenantFormMixin, forms.ModelForm):
    """Minimal, tenant-safe category input for the purchase product workflow."""

    class Meta:
        model = ProductCategory
        fields = ['name', 'default_unit']

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['name'].required = True
        self.fields['default_unit'].required = True
        self.fields['default_unit'].queryset = UnitOfMeasure.objects.filter(
            tenant=organization,
            is_active=True,
        ).order_by('name')

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if ProductCategory.objects.unscoped().filter(
            tenant=self.organization,
            name__iexact=name,
        ).exists():
            raise forms.ValidationError('A category with this name already exists.')
        return name

    def save(self, commit=True):
        category = super().save(commit=False)
        category.measure_unit = self.cleaned_data['default_unit'].label
        if commit:
            category.save()
            category.allowed_units.set([self.cleaned_data['default_unit']])
        return category


class QuickPurchaseProductForm(TenantFormMixin, forms.ModelForm):
    selling_price = forms.DecimalField(required=True, min_value=Decimal('0.01'), decimal_places=2)

    class Meta:
        model = Product
        fields = ['name', 'sku', 'catalog_category', 'sales_unit', 'selling_price', 'brand', 'model_number', 'is_serialized', 'track_expiry']

    def __init__(self, *args, organization=None, allow_extra_fields=False, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['name'].label = 'Name / brand'
        self.fields['name'].widget.attrs['placeholder'] = 'TP-Link Router, Dell Laptop...'
        self.fields['brand'].widget = forms.HiddenInput()
        self.fields['catalog_category'].queryset = ProductCategory.objects.filter(tenant=organization, is_active=True)
        self.fields['sales_unit'].queryset = UnitOfMeasure.objects.filter(tenant=organization, is_active=True)
        if not allow_extra_fields:
            for name in ('sku', 'brand', 'model_number'):
                self.fields[name].disabled = True

    def clean_name(self):
        from products.naming import name_with_brand
        brand = self.data.get(self.add_prefix('brand')) if not self.fields['brand'].disabled else ''
        value = name_with_brand(self.cleaned_data.get('name'), brand)
        self.fields['name'].run_validators(value)
        if Product.objects.unscoped().filter(tenant=self.organization, name__iexact=value, is_active=True).exists():
            raise forms.ValidationError('An active product with this name already exists.')
        return value

    def clean_sku(self):
        from products.sku import validate_manual_sku
        try:
            return validate_manual_sku(self.cleaned_data.get('sku'))
        except forms.ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get('catalog_category')
        unit = cleaned.get('sales_unit')
        if category and unit and not category.allowed_units.filter(pk=unit.pk).exists():
            self.add_error('sales_unit', 'Select a unit allowed by the chosen category.')
        return cleaned

    def save(self, commit=True):
        product = super().save(commit=False)
        product.item_type = Product.ItemType.PHYSICAL
        product.track_stock = True
        product.quantity = Decimal('0.000000')
        product.stock = 0
        product.buying_price = Decimal('0.000000')
        product.selling_price = self.cleaned_data['selling_price']
        product.measure_unit = self.cleaned_data['sales_unit'].label
        product.is_active = True
        if commit:
            from products.creation import save_purchase_product
            save_purchase_product(product, organization=self.organization, supplied_sku=self.cleaned_data.get('sku') or '')
        return product


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


class QuickSupplierForm(TenantFormMixin, forms.ModelForm):
    """Minimal supplier input used only inside the purchasing workspace."""

    class Meta:
        model = Supplier
        fields = ['company_name', 'phone']

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['company_name'].required = True
        self.fields['phone'].required = True

    def clean_company_name(self):
        value = (self.cleaned_data.get('company_name') or '').strip()
        if Supplier.objects.unscoped().filter(tenant=self.organization, company_name__iexact=value).exists():
            raise forms.ValidationError('This supplier already exists in your organization.')
        return value

    def clean_phone(self):
        return (self.cleaned_data.get('phone') or '').strip()


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
    # This is a UI marker, not a trust boundary. It lets the server tell an
    # untouched generated preview from a supplier reference entered by a user.
    auto_generated_reference = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Purchase
        fields = ['supplier', 'reference_number', 'purchase_date', 'notes']
        widgets = {'purchase_date': forms.DateInput(attrs={'type': 'date'}), 'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.filter(tenant=organization, is_active=True)
        self.fields['supplier'].empty_label = 'Select supplier'
        self.fields['reference_number'].widget.attrs.update({
            'autocomplete': 'off',
            'spellcheck': 'false',
            'placeholder': 'Reference number',
        })
        self.fields['reference_number'].help_text = (
            'Generated automatically. You may replace it with the supplier’s delivery or invoice reference.'
        )

    def clean_reference_number(self):
        value = (self.cleaned_data.get('reference_number') or '').strip()
        query = Purchase.objects.unscoped().filter(tenant=self.organization, reference_number__iexact=value)
        if self.instance.pk:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise forms.ValidationError('This purchase reference already exists.')
        return value


class PurchaseLineForm(TenantFormMixin, forms.ModelForm):
    purchase_packaging = forms.ChoiceField(required=False, label='Purchase unit', choices=[('', 'Base unit')])
    purchase_configuration = forms.IntegerField(required=False, widget=forms.HiddenInput)
    product = PurchaseProductChoiceField(
        queryset=Product.objects.none(),
        empty_label='Select a product',
    )

    class Meta:
        model = PurchaseLine
        fields = ['product', 'quantity', 'unit_cost', 'batch_reference', 'expiry_date', 'serial_numbers']
        widgets = {
            'quantity': forms.NumberInput(attrs={'min': '0.000001', 'step': '0.000001'}),
            'unit_cost': forms.NumberInput(attrs={'min': '0', 'step': '0.000001'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'serial_numbers': forms.Textarea(attrs={'rows': 3, 'placeholder': 'One serial per line'}),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['quantity'].required = False
        self.fields['unit_cost'].required = False
        product_ids = set()
        if self.instance and self.instance.product_id:
            product_ids.add(self.instance.product_id)
        if self.is_bound:
            submitted_product = self.data.get(self.add_prefix('product'))
            if submitted_product and str(submitted_product).isdigit():
                product_ids.add(int(submitted_product))
        self.fields['product'].queryset = Product.objects.filter(
            tenant=organization, is_active=True, item_type=Product.ItemType.PHYSICAL, track_stock=True
        ).filter(pk__in=product_ids).select_related('sales_unit').order_by('name', 'sku')
        self.fields['product'].widget.attrs.update({
            'data-purchase-product-select': 'true',
        })
        # The purchase grid owns this select's remote combobox. Prevent the
        # shared local-choice enhancer from creating a second search control.
        self.fields['product'].widget.attrs.pop('data-searchable-select', None)
        self.fields['product'].widget.attrs.pop('data-search-label', None)
        self.fields['product'].widget.attrs.pop('data-search-placeholder', None)
        selected_id = self.data.get(self.add_prefix('product')) if self.is_bound else self.instance.product_id
        selected = self.fields['product'].queryset.filter(pk=selected_id).first() if str(selected_id or '').isdigit() else None
        self.has_packaging = bool(selected and selected.packaging_enabled)
        if self.has_packaging:
            from .packaging_receipts import packaging_options
            self.fields['purchase_packaging'].choices = [('', selected.get_measure_unit_display())] + [
                (str(row['id']), row['label']) for row in packaging_options(selected)]
            self.initial['purchase_configuration'] = selected.packaging_version
            if self.instance.unit_factor is not None:
                self.initial.update(quantity=self.instance.entered_quantity, unit_cost=self.instance.entered_unit_cost,
                    purchase_packaging=str(self.instance.packaging_id or ''), purchase_configuration=self.instance.packaging_version)
        self.fields['quantity'].label = 'Quantity'
        self.fields['unit_cost'].label = 'Unit cost'
        self.fields['batch_reference'].label = 'Batch reference (optional)'
        self.fields['batch_reference'].widget.attrs['placeholder'] = 'e.g. BATCH-001'

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get('product')
        if cleaned.get('quantity') is None:
            self.add_error('quantity', 'Enter the quantity received in the product sales unit.')
        if cleaned.get('unit_cost') is None:
            self.add_error('unit_cost', 'Enter the cost per product sales unit.')
        quantity = cleaned.get('quantity') or Decimal('0.00')
        serials = [
            value.strip()
            for value in (cleaned.get('serial_numbers') or '').replace(',', '\n').splitlines()
            if value.strip()
        ]
        if product and product.packaging_enabled and cleaned.get('quantity') is not None and cleaned.get('unit_cost') is not None:
            from .packaging_receipts import purchase_snapshot
            try:
                snapshot = purchase_snapshot(organization=self.organization, product=product,
                    packaging_id=cleaned.get('purchase_packaging'), quantity=quantity, unit_cost=cleaned['unit_cost'],
                    version=cleaned.get('purchase_configuration'))
                for name, value in snapshot.items():
                    if name in ('quantity', 'unit_cost'):
                        cleaned[name] = value
                    else:
                        setattr(self.instance, name, value)
            except forms.ValidationError as exc:
                self.add_error(None, exc)
        elif product and cleaned.get('purchase_packaging'):
            self.add_error('purchase_packaging', 'This product does not support packaging.')
        elif product and not product.packaging_enabled:
            for name in ('packaging', 'packaging_version', 'unit_factor', 'entered_quantity', 'entered_unit_cost', 'commercial_total', 'selected_unit_snapshot'):
                setattr(self.instance, name, None)
        if product and product.is_serialized:
            if quantity != quantity.to_integral_value():
                self.add_error('quantity', 'Serialized products require a whole-number quantity.')
            elif len(serials) != int(quantity):
                self.add_error('serial_numbers', f'Enter exactly {int(quantity)} unique serial numbers.')
            if len(serials) != len(set(value.upper() for value in serials)):
                self.add_error('serial_numbers', 'Serial numbers must be unique within this line.')
        return cleaned


class PurchaseLineFormSet(BaseInlineFormSet):
    def __init__(self, *args, organization=None, import_present=False, **kwargs):
        self.organization = organization
        self.import_present = import_present
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.organization = organization

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs['organization'] = self.organization
        return kwargs

    def clean(self):
        super().clean()
        active = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get('DELETE')]
        if not active and not self.import_present:
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
        fields = ['customer', 'walk_in_name', 'shipping_address', 'sale_pricing_category', 'discount_amount', 'tax_rate', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Example: Parcel received at the office by Asha Juma, +255 712 345 678.'})}
        help_texts = {
            'customer': 'Leave blank for a walk-in customer.',
            'walk_in_name': 'Optional name to print when no customer record is selected.',
            'shipping_address': 'Optional delivery address for this walk-in sale only. Leave blank for collection.',
            'sale_pricing_category': 'Customer category is automatic unless an authorized override is selected.',
            'discount_amount': 'Cart-wide discount applied after line discounts.',
            'tax_rate': 'VAT percentage applied after discounts.',
            'notes': 'Internal staff note. Record handover details, the receiving person and phone number here. This note is excluded from customer printouts.',
        }

    def save(self, commit=True):
        # A POST of unchanged details is not meaningful activity.
        if self.instance.pk and not self.has_changed():
            return self.instance
        return super().save(commit=commit)

    def __init__(self, *args, organization=None, membership=None, **kwargs):
        self.membership = membership
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['sale_pricing_category'].label = 'Customer category'
        self.fields['customer'].queryset = Customer.objects.filter(tenant=organization, status=Customer.Status.ACTIVE)
        self.fields['customer'].required = False
        if self.instance.sale_pricing_category != Cart.SalePricingCategory.LEGACY_RETAIL:
            self.fields['sale_pricing_category'].choices = [
                choice for choice in Cart.SalePricingCategory.choices
                if choice[0] != Cart.SalePricingCategory.LEGACY_RETAIL
            ]
        can_price = has_tenant_permission(membership.user, organization, PermissionCode.CART_PRICING_OVERRIDE, membership=membership) if membership else False
        can_discount = has_tenant_permission(membership.user, organization, PermissionCode.CART_DISCOUNT_APPLY, membership=membership) if membership else False
        can_edit_tax = has_tenant_permission(membership.user, organization, PermissionCode.CART_TAX_RATE_EDIT, membership=membership) if membership else False
        pricing_grant = permission_grant_for(membership, PermissionCode.CART_PRICING_OVERRIDE)
        if can_price and pricing_grant is not None:
            allowed = {'customer_tier', self.instance.sale_pricing_category, *pricing_grant.allowed_pricing_categories}
            self.fields['sale_pricing_category'].choices = [choice for choice in self.fields['sale_pricing_category'].choices if choice[0] in allowed]
        self.fields['sale_pricing_category'].disabled = not can_price
        self.fields['discount_amount'].disabled = not can_discount
        self.fields['tax_rate'].disabled = not can_edit_tax
        for name in ('sale_pricing_category', 'discount_amount', 'tax_rate'):
            if self.fields[name].disabled:
                self.fields[name].help_text += ' Admin controlled.'
        lines = list(self.instance.lines.all()) if self.instance.pk else []
        gross_subtotal = sum(
            (line.quantity * line.unit_price for line in lines), Decimal('0.00')
        )
        permitted_discount = maximum_discount_for(membership, gross_subtotal)
        if can_discount and permitted_discount is not None:
            line_discounts = sum((line.discount_amount for line in lines), Decimal('0.00'))
            remaining_cart_discount = max(permitted_discount - line_discounts, Decimal('0.00'))
            self.fields['discount_amount'].widget.attrs['max'] = f'{remaining_cart_discount:.2f}'
            self.fields['discount_amount'].help_text += (
                f' Your current total discount limit is TZS {permitted_discount:,.2f}, '
                f'including item discounts; up to TZS {remaining_cart_discount:,.2f} remains here.'
            )

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get('customer')
        if customer and customer.tenant_id != self.organization.id:
            raise forms.ValidationError('Customer must belong to the active tenant.')
        if customer and cleaned.get('shipping_address', '').strip():
            self.add_error('shipping_address', 'This address is for a walk-in sale without a selected customer.')
        if (cleaned.get('discount_amount') or 0) < 0:
            self.add_error('discount_amount', 'Discount cannot be negative.')
        if (cleaned.get('tax_rate') or 0) < 0:
            self.add_error('tax_rate', 'VAT/tax rate cannot be negative.')
        discount = cleaned.get('discount_amount') or Decimal('0.00')
        lines = list(self.instance.lines.all()) if self.instance.pk else []
        gross_subtotal = sum((line.quantity * line.unit_price for line in lines), Decimal('0.00'))
        line_discounts = sum((line.discount_amount for line in lines), Decimal('0.00'))
        error = discount_authorization_error(
            self.membership,
            gross_subtotal=gross_subtotal,
            total_discount=line_discounts + discount,
        )
        if error:
            self.add_error('discount_amount', error)
        if not self.errors and self.instance.pk:
            from products.pricing import BelowCostError, validate_net_unit_price
            subtotal = sum((line.line_total for line in lines), Decimal('0.00'))
            allocated = Decimal('0.00')
            for index, line in enumerate(lines):
                if index == len(lines) - 1:
                    share = discount - allocated
                else:
                    share = discount * line.line_total / subtotal if subtotal else Decimal('0.00')
                    allocated += share
                try:
                    validate_net_unit_price(
                        product=line.product, quantity=line.quantity, unit_price=line.unit_price,
                        line_discount=line.discount_amount, allocated_document_discount=share, base_quantity=line.base_quantity,
                        actor=self.membership.user if self.membership else None,
                        organization=self.organization,
                    )
                except BelowCostError as exc:
                    self.add_error('discount_amount', str(exc))
                    break
        return cleaned


class CartLineDiscountForm(forms.Form):
    discount_amount = forms.DecimalField(
        label='Item discount (TZS)', min_value=Decimal('0.00'), max_digits=14, decimal_places=2,
    )


class CartLineForm(TenantFormMixin, forms.ModelForm):
    sale_packaging = forms.ChoiceField(required=False, label='Selling unit')
    sale_configuration = forms.IntegerField(required=False, widget=forms.HiddenInput)
    serial_units = forms.ModelMultipleChoiceField(queryset=StockUnit.objects.none(), required=False)

    class Meta:
        model = CartLine
        fields = ['product', 'quantity', 'discount_amount']

    def __init__(self, *args, organization=None, sale_pricing_category=Cart.SalePricingCategory.STANDARD,
                 cart=None, membership=None, **kwargs):
        self.sale_pricing_category = sale_pricing_category
        self.cart = cart
        self.membership = membership
        super().__init__(*args, organization=organization, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(tenant=organization, is_active=True).order_by('name')
        product_id = self.data.get('product') if self.is_bound else (
            self.instance.product_id or self.initial.get('product')
        )
        if product_id and str(product_id).isascii() and str(product_id).isdecimal() and len(str(product_id)) <= 18:
            self.fields['serial_units'].queryset = StockUnit.objects.filter(
                tenant=organization, product_id=product_id, status=StockUnit.Status.AVAILABLE
            ).order_by('serial_number')
        if self.instance.pk:
            self.fields['serial_units'].initial = [
                str(value) for value in self.instance.serial_selections.values_list('stock_unit_id', flat=True)
            ]
        self.container_fields = []
        self.selected_packaging_product = self.fields['product'].queryset.filter(pk=product_id).first() if product_id and str(product_id).isdecimal() and len(str(product_id)) <= 18 else None
        self.fields['sale_packaging'].choices = [('', 'Base unit')]
        if self.selected_packaging_product and self.selected_packaging_product.packaging_enabled:
            from .packaging_receipts import packaging_options
            from .models import StockContainer
            product = self.selected_packaging_product
            self.fields['sale_packaging'].choices = [('', product.get_measure_unit_display())] + [
                (str(option['id']), option['label']) for option in packaging_options(product)]
            self.fields['sale_configuration'].initial = self.instance.packaging_version or product.packaging_version
            self.fields['sale_packaging'].initial = str(self.instance.packaging_id or self.initial.get('sale_packaging') or '')
            saved = dict(self.instance.container_selections.values_list('container_id', 'base_quantity')) if self.instance.pk else {}
            containers = StockContainer.objects.filter(tenant=organization, organization=organization, product=product,
                remaining_quantity__gt=0).order_by('received_at', 'pk')[:2000]
            for container in containers:
                name = f'container_{container.pk}'
                self.fields[name] = forms.DecimalField(required=False, decimal_places=0, min_value=0,
                    max_value=container.remaining_quantity, initial=saved.get(container.pk, 0),
                    label=f'{container.label} / {container.get_state_display()} / {container.remaining_quantity:g} {product.get_measure_unit_display()}')
                self.container_fields.append(self[name])
            self.fields['quantity'].widget.attrs.update({'step': '1', 'min': '1'})
        else:
            self.fields['sale_packaging'].widget = forms.HiddenInput()
        self.serials_in_cart = set(CartSerialSelection.objects.filter(
            tenant=organization, cart_line__cart=cart,
        ).exclude(cart_line_id=self.instance.pk).values_list('stock_unit_id', flat=True)) if cart and cart.pk else set()
        can_discount = (
            has_tenant_permission(
                membership.user, organization, PermissionCode.CART_DISCOUNT_APPLY, membership=membership
            ) if membership else False
        )
        self.fields['discount_amount'].disabled = not can_discount
        if not can_discount:
            self.fields['discount_amount'].help_text = 'Administrator controlled.'

    def clean_product(self):
        raw_id = str(self.data.get('product', ''))
        if not (raw_id.isascii() and raw_id.isdecimal() and len(raw_id) <= 18):
            raise forms.ValidationError('Select a valid product.')
        product = self.cleaned_data['product']
        return product

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get('product')
        quantity = cleaned.get('quantity') or Decimal('0.00')
        discount = cleaned.get('discount_amount') or Decimal('0.00')
        base_quantity = quantity
        package = None
        self.aggregate_base_quantity = quantity
        if product and product.packaging_enabled and quantity > 0:
            from .packaging_sales import sale_snapshot, validate_selection
            try:
                values = sale_snapshot(organization=self.organization, product=product,
                    packaging_id=cleaned.get('sale_packaging'), quantity=quantity,
                    version=cleaned.get('sale_configuration'))
                for name, value in values.items():
                    setattr(self.instance, name, value)
                self.instance.product = product
                self.instance.quantity = quantity
                self.instance.tenant = self.organization
                package = values['packaging']
                base_quantity = values['base_quantity']
                others = self.cart.lines.filter(product=product).exclude(pk=self.instance.pk) if self.cart and self.cart.pk else []
                self.aggregate_base_quantity = base_quantity + sum((line.base_quantity or line.quantity for line in others), Decimal('0'))
                used = {}
                for line in others:
                    if not self.instance.pk and line.packaging_id == self.instance.packaging_id:
                        continue
                    for pk, amount in line.container_selections.values_list('container_id', 'base_quantity'):
                        used[pk] = used.get(pk, Decimal('0')) + amount
                self.selected_containers = [(int(name[10:]), cleaned[name]) for name in self.fields
                    if name.startswith('container_') and cleaned.get(name)]
                validate_selection(organization=self.organization, line=self.instance,
                    selections=self.selected_containers, used=used)
            except (ValidationError, ValueError) as exc:
                self.add_error(None, str(exc))
        if quantity <= 0:
            self.add_error(
                'quantity',
                'Quantity must be greater than zero. Use “Remove item” to remove this line.',
            )
        if product and product.is_serialized:
            if quantity != quantity.to_integral_value():
                self.add_error('quantity', 'Serialized items require a whole quantity.')
            if len(cleaned.get('serial_units') or []) != int(quantity):
                self.add_error('serial_units', 'Select one available serial for every unit.')
        if product and product.item_type == Product.ItemType.PHYSICAL and product.track_stock:
            available = product.available_stock
            if available <= 0:
                self.add_error('product', 'This product is currently out of stock.')
            elif (self.aggregate_base_quantity if product.packaging_enabled else quantity) > available:
                self.add_error('quantity', f'Only {available} units are currently available.')
        if product and not product.packaging_enabled and discount > product.price_for_sale_category(
            sale_pricing_category=self.sale_pricing_category, quantity=quantity,
        ) * quantity:
            self.add_error('discount_amount', 'Discount cannot exceed the line amount.')
        if product and self.cart is not None:
            from .services import CartService

            if self.instance.pk and self.cart.lines.filter(product=product, packaging_id=self.instance.packaging_id).exclude(pk=self.instance.pk).exists():
                self.add_error('product', 'This product already has a cart line. Edit that item instead.')
            if not product.is_serialized and cleaned.get('serial_units'):
                self.add_error('serial_units', 'This product does not use serial selections.')

            unit_price, _ = CartService.line_pricing(
                product=product,
                quantity=quantity,
                customer=self.cart.customer,
                sale_pricing_category=self.cart.sale_pricing_category,
                packaging=package, aggregate_base_quantity=self.aggregate_base_quantity,
            )
            from products.pricing import BelowCostError, validate_net_unit_price
            try:
                validate_net_unit_price(
                    product=product, quantity=quantity, unit_price=unit_price,
                    line_discount=discount, base_quantity=base_quantity,
                    actor=self.membership.user if self.membership else None,
                    organization=self.organization,
                )
            except BelowCostError as exc:
                self.add_error('discount_amount', str(exc))
            other_lines = (
                self.cart.lines.exclude(pk=self.instance.pk) if self.cart.pk else CartLine.objects.none()
            )
            gross_subtotal = sum(
                (line.quantity * line.unit_price for line in other_lines), Decimal('0.00')
            ) + quantity * unit_price
            total_discount = sum(
                (line.discount_amount for line in other_lines), Decimal('0.00')
            ) + discount + self.cart.discount_amount
            error = discount_authorization_error(
                self.membership,
                gross_subtotal=gross_subtotal,
                total_discount=total_discount,
            )
            if error:
                self.add_error('discount_amount', error)
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
