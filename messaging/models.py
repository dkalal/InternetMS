from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from users.tenant_models import TenantScopedManager


class MessageTemplate(models.Model):
    class Category(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        QUOTATION = "quotation", "Quotation"
        REMINDER = "reminder", "Reminder"
        SUPPORT = "support", "Support"
        GENERAL = "general", "General"
        RECEIPT = "receipt", "Receipt"

    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="message_templates",
        null=True,
        blank=True,
        db_index=True,
        help_text="Leave blank for a global reusable template.",
    )
    name = models.CharField(max_length=120, db_index=True)
    category = models.CharField(max_length=20, choices=Category.choices, db_index=True)
    content = models.TextField()
    variables_schema = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ["tenant_id", "category", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                condition=models.Q(is_active=True),
                name="uniq_active_template_name_per_tenant",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(tenant__isnull=True, is_active=True),
                name="uniq_active_global_template_name",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "category", "is_active"]),
            models.Index(fields=["category", "is_active"]),
        ]

    def __str__(self) -> str:
        scope = self.tenant.name if self.tenant_id else "Global"
        return f"{self.name} ({scope})"


class WhatsAppManualMessageLog(models.Model):
    class Status(models.TextChoices):
        OPENED = "opened", "Opened"
        SENT_MANUAL = "sent_manual", "Sent manually"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="whatsapp_manual_message_logs",
        db_index=True,
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="whatsapp_manual_message_logs",
    )
    phone_number = models.CharField(max_length=20)
    message_content = models.TextField()
    template_used = models.ForeignKey(
        MessageTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_message_logs",
    )
    related_object_type = models.CharField(max_length=40, blank=True, default="", db_index=True)
    related_object_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="whatsapp_manual_message_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPENED, db_index=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "customer", "created_at"]),
            models.Index(fields=["tenant", "related_object_type", "related_object_id"], name="wa_log_tenant_related_idx"),
            models.Index(fields=["tenant", "status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.tenant_id}:{self.customer_id}:{self.status}:{self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.customer_id and self.tenant_id and self.customer.tenant_id != self.tenant_id:
            raise ValidationError("Message log customer must belong to the same tenant.")
        super().save(*args, **kwargs)
