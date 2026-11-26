from decimal import Decimal
from uuid import uuid4
from django import forms
from django.forms import formset_factory
from internetservices.tailwind import apply_tailwind
from products.models import ProductPackaging, UnitOfMeasure
from .models import ContainerAllocation


class PhysicalCorrectionForm(forms.Form):
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    quantity = forms.DecimalField(max_digits=10, decimal_places=0, min_value=1, label='Physical quantity in base units')
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={'rows': 2}))
    category = forms.ChoiceField(choices=[('damaged', 'Damaged'), ('expired', 'Expired'), ('lost', 'Lost')])
    allocation = forms.ModelChoiceField(queryset=ContainerAllocation.objects.none(), label='Original sold physical piece')

    def __init__(self, *args, organization, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        if invoice is not None:
            del self.fields['category']
            self.fields['allocation'].queryset = ContainerAllocation.objects.filter(tenant=organization,
                billing_line__document=invoice).select_related('container', 'product').order_by('pk')
            self.fields['allocation'].label_from_instance = lambda row: f'{row.product.name} / {row.container.label} / {row.base_quantity:g} base units sold'
        else:
            del self.fields['allocation']
        apply_tailwind(self)


class PackagePriceForm(forms.Form):
    standard_price = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0'))
    technician_price = forms.DecimalField(required=False, max_digits=12, decimal_places=2, min_value=Decimal('0'))
    wholesale_price = forms.DecimalField(required=False, max_digits=12, decimal_places=2, min_value=Decimal('0'))
    retail_price = forms.DecimalField(required=False, max_digits=12, decimal_places=2, min_value=Decimal('0'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class PackageConfigurationForm(PackagePriceForm):
    unit = forms.ModelChoiceField(queryset=UnitOfMeasure.objects.none(), label='Commercial unit')
    factor = forms.DecimalField(max_digits=10, decimal_places=0, min_value=1, label='Base units in one complete package')

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unit'].queryset = UnitOfMeasure.objects.filter(tenant=organization, is_active=True).order_by('name')
        apply_tailwind(self)


class ActivationForm(forms.Form):
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    expected_quantity = forms.DecimalField(max_digits=16, decimal_places=6, widget=forms.HiddenInput)
    expected_average_cost = forms.DecimalField(max_digits=16, decimal_places=6, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={'rows': 2}), label='Physical reconciliation reason')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class CountedContainerForm(forms.Form):
    packaging = forms.TypedChoiceField(choices=(), coerce=int, empty_value=None, required=False, label='Package (leave blank for a loose piece)')
    quantity = forms.DecimalField(max_digits=10, decimal_places=0, min_value=1, label='Counted base quantity in this physical piece')
    sealed = forms.BooleanField(required=False, label='Intact sealed package')
    supplier_identifier = forms.CharField(max_length=100, required=False)
    expiry_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, organization, product, packages=None, **kwargs):
        super().__init__(*args, **kwargs)
        packages = packages if packages is not None else ProductPackaging.objects.filter(tenant=organization, product=product,
            is_active=True, unit__is_active=True, unit__tenant=organization).select_related('unit').order_by('pk')
        self.fields['packaging'].choices = [('', 'Loose base-unit piece')] + [
            (row.pk, f'{row.unit.label} / {row.factor:g} {product.get_measure_unit_display()}') for row in packages
            if row.is_active and row.unit.is_active and row.unit.tenant_id == organization.pk]
        apply_tailwind(self)


CountedContainers = formset_factory(CountedContainerForm, extra=1, max_num=2000, absolute_max=2000, validate_max=True, can_delete=True)
