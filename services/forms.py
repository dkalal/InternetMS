from django import forms

from internetservices.tailwind import apply_tailwind

from .models import Package


class PackageForm(forms.ModelForm):
    class Meta:
        model = Package
        fields = ['name', 'package_type', 'speed', 'monthly_fee', 'setup_fee', 'description', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Enter package description'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)

