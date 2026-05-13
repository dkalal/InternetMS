from django import forms

from internetservices.tailwind import apply_tailwind
from services.models import Package

from .models import Customer, InternetCustomer


class CustomerForm(forms.ModelForm):
    status_change_reason = forms.CharField(
        required=False,
        label='Status change reason',
        help_text='Required when changing customer status. This is saved to the audit trail.',
        widget=forms.Textarea(attrs={'rows': 3, 'data-status-reason': 'true'}),
    )
    packages = forms.ModelMultipleChoiceField(
        queryset=Package.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Service packages',
        help_text='Choose the active packages this customer can use.',
    )

    class Meta:
        model = Customer
        fields = [
            'name',
            'customer_type',
            'status',
            'pricing_tier',
            'email',
            'phone',
            'address',
            'location',
            'ip_address',
            'vlan_id',
            'tin_number',
            'vrn_number',
            'packages',
            'status_change_reason',
        ]
        widgets = {
            'customer_type': forms.Select(attrs={'id': 'customer-type-select'}),
        }
        help_texts = {
            'phone': 'Use an international format when possible, for example +255712345678.',
            'ip_address': 'IPv4 or IPv6 address assigned to this customer.',
            'vlan_id': 'Network VLAN or segment identifier, if applicable.',
            'tin_number': 'Taxpayer Identification Number, if available.',
            'vrn_number': 'VAT Registration Number for VAT-registered customers.',
            'pricing_tier': 'Controls the default pricing behavior used when billing this customer.',
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_status = self.instance.status if self.instance and self.instance.pk else None
        if organization is not None:
            self.fields['packages'].queryset = Package.objects.filter(is_active=True, organization=organization)
        self.fields['name'].widget.attrs.setdefault('placeholder', 'Customer or business name')
        self.fields['location'].widget.attrs.setdefault('placeholder', 'Area, ward, street, or landmark')
        self.fields['vlan_id'].widget.attrs.setdefault('placeholder', 'VLAN 120')
        self.fields['tin_number'].widget.attrs.setdefault('placeholder', 'TIN')
        self.fields['vrn_number'].widget.attrs.setdefault('placeholder', 'VRN')
        apply_tailwind(self)

    def clean_status_change_reason(self):
        reason = (self.cleaned_data.get('status_change_reason') or '').strip()
        new_status = self.cleaned_data.get('status')
        if self.original_status and new_status and self.original_status != new_status and not reason:
            raise forms.ValidationError('Add a reason before changing this customer status.')
        return reason


class InternetCustomerForm(forms.ModelForm):
    class Meta:
        model = InternetCustomer
        fields = ['package_type', 'start_date', 'end_date']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, customer_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
        apply_tailwind(self)


class HardDeleteCustomerForm(forms.Form):
    confirm_phrase = forms.CharField(
        help_text="Type: DELETE <customer_id>",
        label="Confirmation phrase",
    )
    confirm_one = forms.BooleanField(label="I understand this permanently deletes the customer.")
    confirm_two = forms.BooleanField(label="I understand this cannot be undone.")

    def __init__(self, *args, customer_id: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer_id = customer_id
        apply_tailwind(self)

    def clean_confirm_phrase(self):
        phrase = (self.cleaned_data.get("confirm_phrase") or "").strip()
        expected = f"DELETE {self.customer_id}"
        if phrase != expected:
            raise forms.ValidationError(f"Type exactly: {expected}")
        return phrase


class AnonymizeCustomerForm(forms.Form):
    confirm_one = forms.BooleanField(label="I understand this removes customer PII.")
    confirm_two = forms.BooleanField(label="I understand financial history is preserved.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)
