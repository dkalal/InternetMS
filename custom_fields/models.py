from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


class CustomFieldDefinition(models.Model):
    class TargetModel(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        PRODUCT = "product", "Product"
        PACKAGE = "package", "Package"
        SUPPLIER = "supplier", "Supplier"

    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        TEXTAREA = "textarea", "Textarea"
        NUMBER = "number", "Number"
        DATE = "date", "Date"
        BOOLEAN = "boolean", "Boolean"
        CHOICE = "choice", "Choice"

    organization = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="custom_field_definitions",
        null=True,
        blank=True,
        db_index=True,
    )
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_custom_field_definitions",
        null=True,
        blank=True,
        db_index=True,
    )
    target_model = models.CharField(max_length=50, choices=TargetModel.choices, db_index=True)
    key = models.CharField(
        max_length=80,
        validators=[
            RegexValidator(
                regex=r"^[a-z][a-z0-9_]*$",
                message="Use a lowercase key with letters, numbers, and underscores only.",
            )
        ],
    )
    label = models.CharField(max_length=120)
    field_type = models.CharField(max_length=20, choices=FieldType.choices, db_index=True)
    required = models.BooleanField(default=False)
    help_text = models.CharField(max_length=255, blank=True, default="")
    placeholder = models.CharField(max_length=255, blank=True, default="")
    default_value = models.TextField(blank=True, default="")
    choices = models.TextField(
        blank=True,
        default="",
        help_text="One choice per line. Used only for choice fields.",
    )
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    show_on_create = models.BooleanField(default=True)
    show_on_edit = models.BooleanField(default=True)
    show_on_detail = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_custom_field_definitions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["target_model", "display_order", "label", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "target_model", "key"],
                name="uniq_custom_field_key_per_target",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "target_model", "is_active"], name="cf_def_org_target_active_idx"),
            models.Index(fields=["organization", "target_model", "display_order"], name="cf_def_org_target_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.target_model})"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        self.key = (self.key or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def choice_options(self) -> list[str]:
        return [line.strip() for line in self.choices.splitlines() if line.strip()]

    def clean(self):
        errors = {}
        if self.field_type == self.FieldType.CHOICE and not self.choice_options:
            errors["choices"] = "Add at least one choice for a choice field."
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only("field_type", "target_model").first()
            if previous is not None:
                value_count = self.values.count()
                if value_count and previous.field_type != self.field_type:
                    errors["field_type"] = "Changing the field type is blocked after values exist."
                if value_count and previous.target_model != self.target_model:
                    errors["target_model"] = "Changing the target model is blocked after values exist."
        if errors:
            raise ValidationError(errors)


class CustomFieldValue(models.Model):
    organization = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="custom_field_values",
        null=True,
        blank=True,
        db_index=True,
    )
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_custom_field_values",
        null=True,
        blank=True,
        db_index=True,
    )
    field_definition = models.ForeignKey(
        CustomFieldDefinition,
        on_delete=models.PROTECT,
        related_name="values",
    )
    target_model = models.CharField(max_length=50, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    value_text = models.TextField(blank=True, null=True)
    value_number = models.DecimalField(max_digits=18, decimal_places=6, blank=True, null=True)
    value_date = models.DateField(blank=True, null=True)
    value_boolean = models.BooleanField(blank=True, null=True)
    value_json = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["field_definition__display_order", "field_definition__label", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["field_definition", "target_model", "object_id"],
                name="uniq_custom_field_value_per_object",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "target_model", "object_id"], name="cf_value_org_target_object_idx"),
            models.Index(fields=["organization", "field_definition"], name="cf_value_org_definition_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.field_definition.label} for {self.target_model}:{self.object_id}"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        if self.field_definition_id and not self.target_model:
            self.target_model = self.field_definition.target_model
        super().save(*args, **kwargs)

    @property
    def display_value(self) -> str:
        if self.value_boolean is not None:
            return "Yes" if self.value_boolean else "No"
        if self.value_date is not None:
            return self.value_date.isoformat()
        if self.value_number is not None:
            normalized = Decimal(self.value_number)
            formatted = format(normalized, "f")
            return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
        if self.value_text not in (None, ""):
            return self.value_text
        if self.value_json is not None:
            return str(self.value_json)
        return ""
