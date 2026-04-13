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
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Enter product description'}),
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super().__init__(*args, **kwargs)
        if self.organization is not None and 'customer' in self.fields:
            self.fields['customer'].queryset = Customer.objects.filter(organization=self.organization)
        apply_tailwind(self)

    def clean_customer(self):
        customer = self.cleaned_data.get('customer')
        if customer and self.organization and customer.organization_id != self.organization.id:
            raise forms.ValidationError("Invalid customer for the active organization.")
        return customer
