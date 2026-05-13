from django import forms

from internetservices.tailwind import apply_tailwind

from .models import Product
from customers.models import Customer


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            'name',
            'quantity',
            'measure_unit',
            'buying_price',
            'selling_price',
            'retail_price',
            'wholesale_price',
            'wholesale_min_quantity',
            'allow_wholesale',
            'customer',
            'is_active',
            'description',
            'category',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Important specifications, warranty notes, or supplier details'}),
        }
        help_texts = {
            'quantity': 'Current stock quantity available for sale or assignment.',
            'measure_unit': 'Examples: Unit, Meter, Kg, Box.',
            'buying_price': 'Your acquisition cost. Used for margin guidance.',
            'selling_price': 'Default selling price when no retail price is set.',
            'retail_price': 'Customer-facing standard price. Leave blank to use selling price.',
            'wholesale_price': 'Only used when wholesale pricing is enabled.',
            'wholesale_min_quantity': 'Minimum quantity required before wholesale price applies.',
            'customer': 'Optional. Link this product to a specific customer when it is assigned or reserved.',
            'is_active': 'Inactive products stay in history but are hidden from normal selling workflows.',
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super().__init__(*args, **kwargs)
        if self.organization is not None and 'customer' in self.fields:
            self.fields['customer'].queryset = Customer.objects.filter(organization=self.organization)
        self.fields['customer'].empty_label = 'No customer association'
        self.fields['category'].empty_label = None
        self.fields['name'].widget.attrs.setdefault('placeholder', 'Router, radio, cable, software license...')
        self.fields['measure_unit'].widget.attrs.setdefault('placeholder', 'Unit')
        apply_tailwind(self)

    def clean_customer(self):
        customer = self.cleaned_data.get('customer')
        if customer and self.organization and customer.organization_id != self.organization.id:
            raise forms.ValidationError("Invalid customer for the active organization.")
        return customer
