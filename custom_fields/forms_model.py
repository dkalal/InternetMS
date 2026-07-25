from __future__ import annotations

from django import forms
from django.utils.text import slugify

from internetservices.tailwind import apply_tailwind

from .models import CustomFieldDefinition


class CustomFieldDefinitionForm(forms.ModelForm):
    class Meta:
        model = CustomFieldDefinition
        fields = [
            "target_model",
            "key",
            "label",
            "field_type",
            "required",
            "help_text",
            "placeholder",
            "default_value",
            "choices",
            "display_order",
            "is_active",
            "show_on_create",
            "show_on_edit",
            "show_on_detail",
        ]
        widgets = {
            "help_text": forms.Textarea(attrs={"rows": 2}),
            "placeholder": forms.TextInput(),
            "default_value": forms.Textarea(attrs={"rows": 2}),
            "choices": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "key": "Lowercase identifier used to store and load this field.",
            "choices": "One option per line for choice fields.",
            "default_value": "Used only when creating new records.",
        }

    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)
        self.fields["key"].widget.attrs.setdefault("placeholder", "landmark")
        self.fields["label"].widget.attrs.setdefault("placeholder", "Landmark")
        self.fields["label"].help_text = "Shown on the customer, product, or package screen. Use a clear business label."
        self.fields["field_type"].choices = [
            (CustomFieldDefinition.FieldType.TEXT, "📝 Text"),
            (CustomFieldDefinition.FieldType.TEXTAREA, "📝 Multiline text"),
            (CustomFieldDefinition.FieldType.NUMBER, "🔢 Number"),
            (CustomFieldDefinition.FieldType.DATE, "🗓️ Date"),
            (CustomFieldDefinition.FieldType.BOOLEAN, "☑️ Yes / No"),
            (CustomFieldDefinition.FieldType.CHOICE, "🏷️ Choice"),
        ]
        apply_tailwind(self)

    def clean_key(self):
        raw_key = (self.cleaned_data.get("key") or "").strip().lower()
        if raw_key:
            key = raw_key
        else:
            key = slugify((self.cleaned_data.get("label") or "").strip()).replace("-", "_")
        if not key:
            raise forms.ValidationError("Key is required.")
        return key

    def clean(self):
        cleaned = super().clean()
        field_type = cleaned.get("field_type")
        choices = (cleaned.get("choices") or "").strip()
        if field_type == CustomFieldDefinition.FieldType.CHOICE and not choices:
            self.add_error("choices", "Add at least one choice for a choice field.")
        key = cleaned.get("key")
        if self.organization is not None and key:
            queryset = CustomFieldDefinition.objects.filter(
                organization=self.organization,
                target_model=cleaned.get("target_model"),
                key=key,
            )
            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                self.add_error("key", "A field with this key already exists for this section.")
        if self.instance.pk and self.organization is not None:
            from .models import CustomFieldValue

            value_exists = CustomFieldValue.objects.filter(field_definition=self.instance).exists()
            if value_exists:
                original = type(self.instance).objects.filter(pk=self.instance.pk).only("field_type", "target_model").first()
                if original is not None:
                    if original.field_type != field_type:
                        self.add_error("field_type", "Changing the field type is blocked after values exist.")
                    if original.target_model != cleaned.get("target_model"):
                        self.add_error("target_model", "Changing the target model is blocked after values exist.")
        return cleaned
