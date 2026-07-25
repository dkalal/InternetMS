from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.db import transaction

from .models import CustomFieldDefinition, CustomFieldValue


@dataclass(frozen=True)
class CustomFieldSpec:
    definition: CustomFieldDefinition
    field_name: str
    field: forms.Field
    initial: object | None = None


class CustomFieldService:
    @staticmethod
    def get_field_name(definition: CustomFieldDefinition) -> str:
        return f"cf_{definition.key}"

    @staticmethod
    def _resolve_context(context):
        if isinstance(context, dict):
            return context.get("organization"), context.get("mode")
        return context, None

    @classmethod
    def get_fields_for_model(cls, target_model: str, context):
        organization, _mode = cls._resolve_context(context)
        if organization is None:
            return CustomFieldDefinition.objects.none()
        queryset = CustomFieldDefinition.objects.filter(
            organization=organization,
            target_model=target_model,
            is_active=True,
        )
        return queryset.order_by("display_order", "label", "id")

    @classmethod
    def _get_values_map(cls, instance) -> dict[str, CustomFieldValue]:
        values = {}
        queryset = CustomFieldValue.objects.filter(
            organization=instance.organization,
            target_model=instance._meta.model_name,
            object_id=str(instance.pk),
        ).select_related("field_definition")
        for value in queryset:
            values[value.field_definition.key] = value
        return values

    @staticmethod
    def _parse_default(definition: CustomFieldDefinition):
        raw = (definition.default_value or "").strip()
        if raw == "":
            return None
        if definition.field_type in {definition.FieldType.TEXT, definition.FieldType.TEXTAREA, definition.FieldType.CHOICE}:
            return raw
        if definition.field_type == definition.FieldType.NUMBER:
            try:
                return Decimal(raw)
            except (InvalidOperation, ValueError):
                return None
        if definition.field_type == definition.FieldType.DATE:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                return None
        if definition.field_type == definition.FieldType.BOOLEAN:
            return raw.lower() in {"1", "true", "yes", "on"}
        return raw

    @classmethod
    def _value_to_python(cls, definition: CustomFieldDefinition, value):
        if definition.field_type in {definition.FieldType.TEXT, definition.FieldType.TEXTAREA, definition.FieldType.CHOICE}:
            text = "" if value is None else str(value).strip()
            return text or None
        if definition.field_type == definition.FieldType.NUMBER:
            if value in (None, ""):
                return None
            if isinstance(value, Decimal):
                return value
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ValidationError({cls.get_field_name(definition): "Enter a valid number."}) from exc
        if definition.field_type == definition.FieldType.DATE:
            if value in (None, ""):
                return None
            if isinstance(value, date):
                return value
            try:
                return date.fromisoformat(str(value))
            except ValueError as exc:
                raise ValidationError({cls.get_field_name(definition): "Enter a valid date."}) from exc
        if definition.field_type == definition.FieldType.BOOLEAN:
            if value in (None, ""):
                return None
            return bool(value)
        return value

    @classmethod
    def _build_form_field(cls, definition: CustomFieldDefinition, *, initial=None) -> forms.Field:
        common_kwargs = {
            "label": definition.label,
            "required": definition.required,
            "help_text": definition.help_text,
            "initial": initial,
        }
        if definition.field_type == definition.FieldType.TEXT:
            field = forms.CharField(widget=forms.TextInput(attrs={"placeholder": definition.placeholder}), **common_kwargs)
        elif definition.field_type == definition.FieldType.TEXTAREA:
            field = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "placeholder": definition.placeholder}), **common_kwargs)
        elif definition.field_type == definition.FieldType.NUMBER:
            field = forms.DecimalField(
                max_digits=18,
                decimal_places=6,
                widget=forms.NumberInput(attrs={"step": "0.000001", "placeholder": definition.placeholder}),
                **common_kwargs,
            )
        elif definition.field_type == definition.FieldType.DATE:
            field = forms.DateField(
                widget=forms.DateInput(attrs={"type": "date", "placeholder": definition.placeholder}),
                **common_kwargs,
            )
        elif definition.field_type == definition.FieldType.BOOLEAN:
            field = forms.BooleanField(widget=forms.CheckboxInput(), **common_kwargs)
        else:
            choices = [(choice, choice) for choice in definition.choice_options]
            field = forms.ChoiceField(choices=choices, widget=forms.Select(), **common_kwargs)
        return field

    @classmethod
    def prepare_custom_fields_for_form(cls, *, instance=None, organization=None, target_model: str, mode: str) -> list[CustomFieldSpec]:
        if organization is None:
            return []
        definitions = cls.get_fields_for_model(target_model, {"organization": organization, "mode": mode})
        existing_values = cls._get_values_map(instance) if instance and instance.pk else {}
        specs: list[CustomFieldSpec] = []
        for definition in definitions:
            if mode == "create" and not definition.show_on_create:
                continue
            if mode == "edit" and not definition.show_on_edit:
                continue
            value_obj = existing_values.get(definition.key)
            initial = None
            if value_obj is not None:
                if definition.field_type == definition.FieldType.TEXT:
                    initial = value_obj.value_text
                elif definition.field_type == definition.FieldType.TEXTAREA:
                    initial = value_obj.value_text
                elif definition.field_type == definition.FieldType.NUMBER:
                    initial = value_obj.value_number
                elif definition.field_type == definition.FieldType.DATE:
                    initial = value_obj.value_date
                elif definition.field_type == definition.FieldType.BOOLEAN:
                    initial = value_obj.value_boolean
                elif definition.field_type == definition.FieldType.CHOICE:
                    initial = value_obj.value_text
            elif not instance or not instance.pk:
                initial = cls._parse_default(definition)
            field_name = cls.get_field_name(definition)
            field = cls._build_form_field(definition, initial=initial)
            specs.append(CustomFieldSpec(definition=definition, field_name=field_name, field=field, initial=initial))
        return specs

    @classmethod
    def validate_custom_field_input(cls, *, target_model: str, submitted_data, organization, instance=None) -> dict[str, object]:
        if organization is None:
            return {}
        definitions = list(cls.get_fields_for_model(target_model, {"organization": organization}))
        definition_map = {cls.get_field_name(definition): definition for definition in definitions}
        cleaned: dict[str, object] = {}
        errors: dict[str, str] = {}

        for field_name, value in submitted_data.items():
            if not field_name.startswith("cf_"):
                continue
            definition = definition_map.get(field_name)
            if definition is None:
                errors[field_name] = "This custom field is inactive or unavailable."
                continue
            normalized = cls._value_to_python(definition, value)
            if definition.field_type == definition.FieldType.BOOLEAN and definition.required and not normalized:
                errors[field_name] = "This field is required."
                continue
            if definition.field_type == definition.FieldType.BOOLEAN and value is False and not definition.required:
                cleaned[field_name] = False
                continue
            if definition.required and normalized in (None, "", []):
                errors[field_name] = "This field is required."
                continue
            if definition.field_type == definition.FieldType.CHOICE and normalized not in (None, ""):
                if normalized not in definition.choice_options:
                    errors[field_name] = "Select a valid choice."
                    continue
            cleaned[field_name] = normalized

        if errors:
            raise ValidationError(errors)
        return cleaned

    @classmethod
    def save_custom_field_values(cls, instance, submitted_data, user=None):
        organization = getattr(instance, "organization", None) or getattr(instance, "tenant", None)
        if organization is None:
            return []
        target_model = instance._meta.model_name
        cleaned = cls.validate_custom_field_input(
            target_model=target_model,
            submitted_data=submitted_data,
            organization=organization,
            instance=instance,
        )
        if not cleaned:
            return []

        definitions = {
            cls.get_field_name(definition): definition
            for definition in cls.get_fields_for_model(target_model, {"organization": organization})
        }
        saved_values = []

        with transaction.atomic():
            for field_name, value in cleaned.items():
                definition = definitions[field_name]
                if value in (None, "", []):
                    CustomFieldValue.objects.filter(
                        field_definition=definition,
                        target_model=target_model,
                        object_id=str(instance.pk),
                    ).delete()
                    continue
                defaults = {
                    "organization": organization,
                    "tenant": organization,
                    "target_model": target_model,
                }
                field_value, _created = CustomFieldValue.objects.select_for_update().get_or_create(
                    field_definition=definition,
                    target_model=target_model,
                    object_id=str(instance.pk),
                    defaults=defaults,
                )
                field_value.organization = organization
                field_value.tenant = organization
                field_value.target_model = target_model
                field_value.object_id = str(instance.pk)
                field_value.value_text = None
                field_value.value_number = None
                field_value.value_date = None
                field_value.value_boolean = None
                field_value.value_json = None
                if definition.field_type in {definition.FieldType.TEXT, definition.FieldType.TEXTAREA, definition.FieldType.CHOICE}:
                    field_value.value_text = value
                elif definition.field_type == definition.FieldType.NUMBER:
                    field_value.value_number = value
                elif definition.field_type == definition.FieldType.DATE:
                    field_value.value_date = value
                elif definition.field_type == definition.FieldType.BOOLEAN:
                    field_value.value_boolean = value
                field_value.save()
                saved_values.append(field_value)
        return saved_values

    @classmethod
    def get_custom_field_values(cls, instance):
        organization = getattr(instance, "organization", None) or getattr(instance, "tenant", None)
        if organization is None or not getattr(instance, "pk", None):
            return []
        target_model = instance._meta.model_name
        definitions = list(
            CustomFieldDefinition.objects.filter(
                organization=organization,
                target_model=target_model,
            ).order_by("display_order", "label", "id")
        )
        definition_ids = {definition.id for definition in definitions}
        values = {
            value.field_definition_id: value
            for value in CustomFieldValue.objects.filter(
                organization=organization,
                target_model=target_model,
                object_id=str(instance.pk),
            ).select_related("field_definition")
        }
        rendered = []
        for definition in definitions:
            value = values.get(definition.id)
            if value is None and not definition.show_on_detail:
                continue
            display_value = value.display_value if value is not None else cls._parse_default(definition)
            if isinstance(display_value, bool):
                display_value = "Yes" if display_value else "No"
            elif display_value is not None:
                display_value = str(display_value)
            rendered.append(
                {
                    "definition": definition,
                    "field_name": cls.get_field_name(definition),
                    "raw_value": value,
                    "display_value": display_value or "",
                    "show_on_detail": definition.show_on_detail,
                    "is_active": definition.is_active,
                }
            )
        for value in CustomFieldValue.objects.filter(
            organization=organization,
            target_model=target_model,
            object_id=str(instance.pk),
        ).select_related("field_definition").order_by("field_definition__display_order", "field_definition__label", "id"):
            if value.field_definition_id in definition_ids:
                continue
            rendered.append(
                {
                    "definition": value.field_definition,
                    "field_name": cls.get_field_name(value.field_definition),
                    "raw_value": value,
                    "display_value": value.display_value,
                    "show_on_detail": value.field_definition.show_on_detail,
                    "is_active": value.field_definition.is_active,
                }
            )
        return rendered

    @classmethod
    def build_inline_definition_form(cls, *, request, organization, target_model: str | None):
        from .forms_model import CustomFieldDefinitionForm

        modal_token = request.GET.get("cf_modal")
        modal_data = None
        modal_open = False

        if modal_token:
            modal_data = request.session.pop(f"custom_field_modal:{modal_token}", None)
            request.session.modified = True
            modal_open = modal_data is not None

        form = CustomFieldDefinitionForm(data=modal_data, organization=organization)
        if target_model and "target_model" in form.fields:
            form.fields["target_model"].widget = forms.HiddenInput()
            if not form.is_bound:
                form.initial["target_model"] = target_model
        return form, modal_open

    @classmethod
    def render_custom_field_input_html(cls, definition: CustomFieldDefinition, *, initial=None) -> str:
        field_name = cls.get_field_name(definition)
        form = forms.Form()
        form.fields[field_name] = cls._build_form_field(definition, initial=initial)
        if initial is not None:
            form.initial[field_name] = initial
        return render_to_string(
            "includes/custom_field_input.html",
            {
                "field": form[field_name],
                "definition": definition,
                "field_name": field_name,
            },
        )
