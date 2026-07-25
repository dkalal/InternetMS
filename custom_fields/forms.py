from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from .services import CustomFieldService


class CustomFieldFormMixin:
    custom_field_target_model = None

    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        self.custom_field_specs = []
        self.custom_field_names = []
        self.cleaned_custom_field_data = {}
        super().__init__(*args, **kwargs)

        target_model = self.custom_field_target_model or getattr(getattr(self._meta, "model", None), "_meta", None)
        if hasattr(target_model, "model_name"):
            target_model = target_model.model_name
        if not target_model:
            return

        mode = "edit" if getattr(self.instance, "pk", None) else "create"
        self.custom_field_specs = CustomFieldService.prepare_custom_fields_for_form(
            instance=self.instance if getattr(self.instance, "pk", None) else None,
            organization=self.organization,
            target_model=target_model,
            mode=mode,
        )
        for spec in self.custom_field_specs:
            self.fields[spec.field_name] = spec.field
            if not self.is_bound and spec.initial is not None:
                self.initial.setdefault(spec.field_name, spec.initial)
        self.custom_field_names = [spec.field_name for spec in self.custom_field_specs]

    def clean(self):
        cleaned = super().clean()
        if not self.custom_field_specs:
            self.cleaned_custom_field_data = {}
            return cleaned
        target_model = self.custom_field_target_model or getattr(getattr(self._meta, "model", None), "_meta", None)
        if hasattr(target_model, "model_name"):
            target_model = target_model.model_name
        submitted_data = {spec.field_name: cleaned.get(spec.field_name) for spec in self.custom_field_specs}
        try:
            self.cleaned_custom_field_data = CustomFieldService.validate_custom_field_input(
                target_model=target_model,
                submitted_data=submitted_data,
                organization=self.organization,
                instance=self.instance if getattr(self.instance, "pk", None) else None,
            )
        except ValidationError as exc:
            error_dict = getattr(exc, "error_dict", None)
            if error_dict:
                for field_name, errors in error_dict.items():
                    for error in errors:
                        self.add_error(field_name, error)
                raise
            raise
        return cleaned

    def save_custom_fields(self, instance, *, user=None):
        if not self.cleaned_custom_field_data:
            return []
        return CustomFieldService.save_custom_field_values(instance, self.cleaned_custom_field_data, user=user)
