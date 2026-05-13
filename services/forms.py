from django import forms

from internetservices.tailwind import apply_tailwind

from .models import Package


class PackageForm(forms.ModelForm):
    class Meta:
        model = Package
        fields = ['name', 'package_type', 'speed', 'monthly_fee', 'setup_fee', 'description', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Coverage notes, installation requirements, or customer-facing details'}),
        }
        help_texts = {
            'speed': 'Use a short format like 10 Mbps or 50 Mbps.',
            'monthly_fee': 'Recurring subscription amount charged per month.',
            'setup_fee': 'One-time installation or activation charge.',
            'is_active': 'Inactive packages remain in history but are hidden from normal assignment workflows.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs.setdefault('placeholder', 'Home 10 Mbps, Business 50 Mbps...')
        self.fields['speed'].widget.attrs.setdefault('placeholder', '10 Mbps')
        apply_tailwind(self)
