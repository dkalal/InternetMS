from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from users.tenant_models import TenantScopedManager, TenantScopedQuerySet


class WorkReportQuerySet(TenantScopedQuerySet):
    def update(self, **kwargs):
        raise ValidationError("Work reports must be changed through lifecycle services.")

    def delete(self):
        raise ValidationError("Work reports are retained as business history and cannot be deleted.")


class TechnicianWorkReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Awaiting review"
        REJECTED = "REJECTED", "Needs correction"
        APPROVED = "APPROVED", "Approved"

    tenant = models.ForeignKey(
        "users.Organization", on_delete=models.PROTECT,
        related_name="technician_work_reports", db_index=True,
    )
    technician = models.ForeignKey(
        "users.TenantMembership", on_delete=models.PROTECT,
        related_name="work_reports", db_index=True,
    )
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.PROTECT,
        related_name="technician_work_reports", null=True, blank=True,
    )
    work_title = models.CharField(max_length=180)
    client_name = models.CharField(max_length=200)
    service_date = models.DateField(db_index=True)
    work_location = models.CharField(max_length=255, blank=True, default="")
    activity_description = models.TextField()
    agreed_amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)],
    )
    internal_notes = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True,
    )
    rejection_reason = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    approved_by = models.ForeignKey(
        "users.TenantMembership", on_delete=models.PROTECT,
        related_name="approved_work_reports", null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager.from_queryset(WorkReportQuerySet)()

    class Meta:
        ordering = ["-service_date", "-id"]
        indexes = [
            models.Index(fields=["tenant", "technician", "status"]),
            models.Index(fields=["tenant", "status", "submitted_at"]),
            models.Index(fields=["tenant", "service_date"]),
        ]

    def __str__(self):
        return f"{self.work_title} - {self.technician.user}"

    def clean(self):
        errors = {}
        if self.service_date and self.service_date > timezone.localdate():
            errors["service_date"] = "A Work Report can be created only after the work is completed."
        if self.technician_id:
            if self.technician.tenant_id != self.tenant_id:
                errors["technician"] = "Technician membership belongs to another tenant."
            if self.technician.base_role != self.technician.BaseRole.TECHNICIAN:
                errors["technician"] = "The report owner must be a Technician."
        if self.customer_id and self.customer.tenant_id != self.tenant_id:
            errors["customer"] = "Customer belongs to another tenant."
        if self.approved_by_id:
            is_support_admin = self.approved_by.base_role == self.approved_by.BaseRole.SUPER_ADMIN
            if self.approved_by.tenant_id != self.tenant_id and not is_support_admin:
                errors["approved_by"] = "Approver belongs to another tenant."
        if self.status == self.Status.REJECTED and not self.rejection_reason.strip():
            errors["rejection_reason"] = "A rejection reason is required."
        if self.status == self.Status.APPROVED and (not self.approved_by_id or not self.approved_at):
            errors["status"] = "Approved reports require an approver and approval timestamp."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.unscoped().filter(pk=self.pk).first()
            if previous and previous.status == self.Status.APPROVED and not getattr(self, "_approved_correction", False):
                protected = (
                    "technician_id", "customer_id", "work_title", "client_name", "service_date",
                    "work_location", "activity_description", "agreed_amount", "internal_notes",
                    "status", "rejection_reason", "submitted_at", "approved_at", "approved_by_id",
                )
                if any(getattr(previous, field) != getattr(self, field) for field in protected):
                    raise ValidationError("Approved work reports are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Work reports are retained as business history and cannot be deleted.")

    def snapshot(self):
        return {
            "work_title": self.work_title,
            "client_name": self.client_name,
            "customer_id": self.customer_id,
            "service_date": self.service_date.isoformat(),
            "work_location": self.work_location,
            "activity_description": self.activity_description,
            "agreed_amount": str(self.agreed_amount),
            "internal_notes": self.internal_notes,
            "status": self.status,
            "rejection_reason": self.rejection_reason,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by_id": self.approved_by_id,
        }


class ImmutableHistoryQuerySet(TenantScopedQuerySet):
    def update(self, **kwargs):
        raise ValidationError("Work report history is immutable.")

    def delete(self):
        raise ValidationError("Work report history is immutable.")


class WorkReportHistory(models.Model):
    class Event(models.TextChoices):
        CREATED = "CREATED", "Created"
        UPDATED = "UPDATED", "Draft updated"
        SUBMITTED = "SUBMITTED", "Submitted"
        RESUBMITTED = "RESUBMITTED", "Resubmitted"
        REJECTED = "REJECTED", "Rejected"
        APPROVED = "APPROVED", "Approved"
        CORRECTED = "CORRECTED", "Approved report corrected"

    tenant = models.ForeignKey(
        "users.Organization", on_delete=models.PROTECT,
        related_name="work_report_history", db_index=True,
    )
    report = models.ForeignKey(
        TechnicianWorkReport, on_delete=models.PROTECT, related_name="history",
    )
    actor_membership = models.ForeignKey(
        "users.TenantMembership", on_delete=models.PROTECT,
        related_name="work_report_history_events",
    )
    event = models.CharField(max_length=20, choices=Event.choices, db_index=True)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = TenantScopedManager.from_queryset(ImmutableHistoryQuerySet)()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "report", "created_at"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Work report history is immutable.")
        if self.report_id and self.report.tenant_id != self.tenant_id:
            raise ValidationError("History and report tenants must match.")
        is_support_admin = self.actor_membership.base_role == self.actor_membership.BaseRole.SUPER_ADMIN
        if self.actor_membership.tenant_id != self.tenant_id and not is_support_admin:
            raise ValidationError("History actor belongs to another tenant.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Work report history is immutable.")
