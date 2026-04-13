from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm

from internetservices.tailwind import apply_tailwind
from .models import OrganizationBranding
User = get_user_model()

class UserRegisterForm(UserCreationForm):
    email = forms.EmailField()  # Add email field which is not in the default UserCreationForm

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class TailwindAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class TailwindPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class OrganizationBrandingForm(forms.ModelForm):
    class Meta:
        model = OrganizationBranding
        fields = [
            "legal_name",
            "address_line1",
            "address_line2",
            "phone",
            "email",
            "tin_number",
            "vrn_number",
            "bank_details",
            "footer_note",
            "logo",
        ]
        widgets = {
            "bank_details": forms.Textarea(attrs={"rows": 4}),
            "footer_note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if not logo:
            return logo
        content_type = getattr(logo, "content_type", "") or ""
        if not content_type.startswith("image/"):
            raise forms.ValidationError("Logo must be an image file.")
        max_size_bytes = 2 * 1024 * 1024
        if getattr(logo, "size", 0) > max_size_bytes:
            raise forms.ValidationError("Logo must be 2MB or smaller.")
        return logo
