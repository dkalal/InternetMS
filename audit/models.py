from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from users.tenant_models import TenantScopedManager


class AuditLog(models.Model):
    """
    Immutable audit trail for critical actions.

    Guardrails:
    - Tenant-scoped via `organization`
    - Append-only (no update paths in code)
    """

    organization = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="audit_logs",
        db_index=True,
    )
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_audit_logs",
        null=True,
        blank=True,
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="performed_audit_logs",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=80, db_index=True)
    action_type = models.CharField(max_length=80, db_index=True, blank=True, default="")
    object_type = models.CharField(max_length=80, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    document_id = models.CharField(max_length=64, db_index=True, blank=True, default="")
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    performed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["organization", "action", "created_at"]),
            models.Index(fields=["organization", "object_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.action}:{self.object_type}:{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit logs are immutable.")
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        if self.performed_by_id is None and self.actor_id is not None:
            self.performed_by_id = self.actor_id
        if self.actor_id is None and self.performed_by_id is not None:
            self.actor_id = self.performed_by_id
        if not self.action_type:
            self.action_type = self.action
        if not self.action:
            self.action = self.action_type
        if not self.document_id:
            self.document_id = self.object_id
        if not self.object_id:
            self.object_id = self.document_id
        if self.performed_at is None:
            self.performed_at = timezone.now()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit logs are immutable.")
