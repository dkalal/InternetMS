from __future__ import annotations

from calendar import month_abbr
from datetime import timedelta

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


class WorkReportServiceDayQuerySet(TenantScopedQuerySet):
    def update(self, **kwargs):
        raise ValidationError("Work dates must be changed through Work Report services.")

    def delete(self):
        raise ValidationError("Work dates must be changed through Work Report services.")


class ImmutablePaymentQuerySet(TenantScopedQuerySet):
    def update(self, **kwargs):
        raise ValidationError("Technician payment records must be changed through lifecycle services.")

    def delete(self):
        raise ValidationError("Technician payment records are retained as business history and cannot be deleted.")


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
    # A work report can cover internal, preventative, or unassigned work, so a
    # free-text client/company reference must not be required. Keep an empty
    # string rather than NULL to preserve the existing text-field convention.
    client_name = models.CharField(max_length=200, blank=True, default="")
    # Backward-compatible ordering field containing the earliest related work
    # date. The service-day rows are authoritative; remove this only in a later,
    # separately verified migration.
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

    def ordered_service_days(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("service_days")
        if prefetched is not None:
            return sorted(prefetched, key=lambda item: (item.service_date, item.pk or 0))
        if not self.pk:
            return []
        return list(WorkReportServiceDay.objects.unscoped().filter(
            tenant_id=self.tenant_id, report_id=self.pk,
        ).order_by("service_date", "id"))

    @property
    def service_days_summary(self):
        dates = [row.service_date for row in self.ordered_service_days()]
        if not dates and self.service_date:
            dates = [self.service_date]
        return format_work_dates(dates)

    def snapshot(self):
        service_days = self.ordered_service_days()
        service_day_snapshot = [
            {
                "service_date": day.service_date.isoformat(),
                "activity_note": day.activity_note,
            }
            for day in service_days
        ]
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
            "service_days": service_day_snapshot,
        }


def _short_date(value, *, include_year=True):
    result = f"{value.day:02d} {month_abbr[value.month]}"
    return f"{result} {value.year}" if include_year else result


def _date_span(first, last):
    if first.year == last.year and first.month == last.month:
        return f"{first.day:02d}–{last.day:02d} {month_abbr[first.month]} {first.year}"
    if first.year == last.year:
        return f"{first.day:02d} {month_abbr[first.month]}–{last.day:02d} {month_abbr[last.month]} {first.year}"
    return f"{_short_date(first)}–{_short_date(last)}"


def format_work_dates(values):
    """Return a compact, explicit summary of chronological work dates."""
    dates = sorted(set(values))
    count = len(dates)
    if not dates:
        return "No work dates"
    if count == 1:
        return _short_date(dates[0])
    if count > 3:
        return f"{_date_span(dates[0], dates[-1])} · {count} work days"
    consecutive = all(
        current - previous == timedelta(days=1)
        for previous, current in zip(dates, dates[1:])
    )
    if consecutive:
        return f"{_date_span(dates[0], dates[-1])} · {count} days"
    if count <= 3 and len({(item.year, item.month) for item in dates}) == 1:
        day_list = ", ".join(f"{item.day:02d}" for item in dates[:-1])
        day_list = f"{day_list} & {dates[-1].day:02d}"
        return f"{day_list} {month_abbr[dates[0].month]} {dates[0].year} · {count} days"
    if count <= 3:
        labels = [_short_date(item) for item in dates]
        return f"{', '.join(labels[:-1])} & {labels[-1]} · {count} days"
    return f"{_date_span(dates[0], dates[-1])} · {count} work days"


class WorkReportServiceDay(models.Model):
    tenant = models.ForeignKey(
        "users.Organization", on_delete=models.PROTECT,
        related_name="work_report_service_days", db_index=True,
    )
    report = models.ForeignKey(
        TechnicianWorkReport, on_delete=models.PROTECT, related_name="service_days",
    )
    service_date = models.DateField(db_index=True)
    activity_note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager.from_queryset(WorkReportServiceDayQuerySet)()

    class Meta:
        ordering = ["service_date", "id"]
        indexes = [
            models.Index(
                fields=["tenant", "service_date"], name="wr_day_tenant_date_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "service_date"],
                name="unique_work_report_service_date",
            ),
        ]

    def __str__(self):
        return f"{self.report_id} - {self.service_date:%Y-%m-%d}"

    def clean(self):
        errors = {}
        if self.service_date and self.service_date > timezone.localdate():
            errors["service_date"] = "Work dates cannot be in the future."
        if self.report_id and self.report.tenant_id != self.tenant_id:
            errors["tenant"] = "Work date and Work Report tenants must match."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.report_id and not getattr(self, "_service_write", False):
            report_status = TechnicianWorkReport.objects.unscoped().filter(
                pk=self.report_id,
            ).values_list("status", flat=True).first()
            if report_status not in {
                TechnicianWorkReport.Status.DRAFT,
                TechnicianWorkReport.Status.REJECTED,
            }:
                raise ValidationError("Work dates on submitted or approved reports are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not getattr(self, "_service_write", False):
            raise ValidationError("Work dates must be changed through Work Report services.")
        return super().delete(*args, **kwargs)


class TechnicianPaymentRecord(models.Model):
    class Status(models.TextChoices):
        AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION", "Awaiting Technician confirmation"
        CONFIRMED = "CONFIRMED", "Received confirmed"
        DISPUTED = "DISPUTED", "Disputed"
        VOIDED = "VOIDED", "Voided"

    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        OTHER = "OTHER", "Other"

    tenant = models.ForeignKey(
        "users.Organization", on_delete=models.PROTECT,
        related_name="technician_payment_records", db_index=True,
    )
    report = models.ForeignKey(
        TechnicianWorkReport, on_delete=models.PROTECT, related_name="payment_records",
    )
    agreed_amount_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2)
    payment_date = models.DateField(db_index=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    reference = models.CharField(max_length=200, blank=True, default="")
    manager_note = models.TextField(blank=True, default="")
    adjustment_reason = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=24, choices=Status.choices,
        default=Status.AWAITING_CONFIRMATION, db_index=True,
    )
    recorded_by = models.ForeignKey(
        "users.TenantMembership", on_delete=models.PROTECT,
        related_name="technician_payments_recorded",
    )
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    disputed_at = models.DateTimeField(null=True, blank=True)
    dispute_reason = models.TextField(blank=True, default="")
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        "users.TenantMembership", on_delete=models.PROTECT,
        related_name="technician_payments_voided", null=True, blank=True,
    )
    void_reason = models.TextField(blank=True, default="")
    replaces = models.OneToOneField(
        "self", on_delete=models.PROTECT, related_name="replacement",
        null=True, blank=True,
    )
    batch = models.ForeignKey(
        "TechnicianPaymentBatch", on_delete=models.PROTECT,
        related_name="allocations", null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager.from_queryset(ImmutablePaymentQuerySet)()

    class Meta:
        ordering = ["-recorded_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "status", "recorded_at"]),
            models.Index(fields=["tenant", "report", "recorded_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_paid__gt=0),
                name="technician_payment_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(amount_paid=models.F("agreed_amount_snapshot"))
                    | ~models.Q(adjustment_reason="")
                ),
                name="technician_payment_adjustment_reason_required",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="CONFIRMED") | models.Q(confirmed_at__isnull=False),
                name="technician_payment_confirmed_at_required",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="DISPUTED")
                    | (models.Q(disputed_at__isnull=False) & ~models.Q(dispute_reason=""))
                ),
                name="technician_payment_dispute_fields_required",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="VOIDED")
                    | (
                        models.Q(voided_at__isnull=False)
                        & models.Q(voided_by__isnull=False)
                        & ~models.Q(void_reason="")
                    )
                ),
                name="technician_payment_void_fields_required",
            ),
            models.UniqueConstraint(
                fields=["report"], condition=~models.Q(status="VOIDED"),
                name="one_active_technician_payment_per_report",
            ),
        ]

    def __str__(self):
        return f"Technician payment {self.pk or 'new'} for report {self.report_id}"

    @property
    def difference(self):
        return self.amount_paid - self.agreed_amount_snapshot

    def clean(self):
        errors = {}
        if self.amount_paid is not None and self.amount_paid <= 0:
            errors["amount_paid"] = "Amount paid must be greater than zero."
        if self.report_id:
            if self.report.tenant_id != self.tenant_id:
                errors["report"] = "Payment and Work Report tenants must match."
            if self.report.status != TechnicianWorkReport.Status.APPROVED:
                errors["report"] = "Only an approved Work Report can have a payment record."
        if (
            self.amount_paid is not None
            and self.agreed_amount_snapshot is not None
            and self.amount_paid != self.agreed_amount_snapshot
            and not self.adjustment_reason.strip()
        ):
            errors["adjustment_reason"] = "An adjustment reason is required when the paid amount differs."
        if self.recorded_by_id and self.recorded_by.tenant_id != self.tenant_id:
            errors["recorded_by"] = "Recording Manager belongs to another tenant."
        if self.status == self.Status.CONFIRMED and not self.confirmed_at:
            errors["confirmed_at"] = "Confirmed payments require a confirmation timestamp."
        if self.status == self.Status.DISPUTED and (
            not self.disputed_at or not self.dispute_reason.strip()
        ):
            errors["dispute_reason"] = "Disputed payments require a timestamp and reason."
        if self.status == self.Status.VOIDED and (
            not self.voided_at or not self.voided_by_id or not self.void_reason.strip()
        ):
            errors["void_reason"] = "Voided payments require an actor, timestamp, and reason."
        if self.voided_by_id and self.voided_by.tenant_id != self.tenant_id:
            errors["voided_by"] = "Voiding Manager belongs to another tenant."
        if self.replaces_id:
            if self.replaces_id == self.pk:
                errors["replaces"] = "A payment cannot replace itself."
            elif (
                self.replaces.status != self.Status.VOIDED
                or self.replaces.tenant_id != self.tenant_id
                or self.replaces.report_id != self.report_id
                or self.replaces.report.technician_id != self.report.technician_id
            ):
                errors["replaces"] = "A replacement must reference a voided payment for the same Work Report."
        if self.batch_id:
            if (
                self.batch.tenant_id != self.tenant_id
                or self.batch.technician_id != self.report.technician_id
                or self.batch.status != self.status
                or self.batch.payment_date != self.payment_date
                or self.batch.payment_method != self.payment_method
            ):
                errors["batch"] = "Payment allocation and batch details must remain consistent."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_lifecycle_transition", False):
            raise ValidationError("Technician payment records must be changed through lifecycle services.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Technician payment records are retained as business history and cannot be deleted.")

    def snapshot(self):
        return {
            "payment_record_id": self.pk,
            "report_id": self.report_id,
            "technician_membership_id": self.report.technician_id,
            "agreed_amount_snapshot": str(self.agreed_amount_snapshot),
            "amount_paid": str(self.amount_paid),
            "difference": str(self.difference),
            "payment_date": self.payment_date.isoformat(),
            "payment_method": self.payment_method,
            "reference": self.reference,
            "manager_note": self.manager_note,
            "adjustment_reason": self.adjustment_reason,
            "status": self.status,
            "recorded_by_id": self.recorded_by_id,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "disputed_at": self.disputed_at.isoformat() if self.disputed_at else None,
            "dispute_reason": self.dispute_reason,
            "voided_at": self.voided_at.isoformat() if self.voided_at else None,
            "voided_by_id": self.voided_by_id,
            "void_reason": self.void_reason,
            "replaces_id": self.replaces_id,
            "batch_id": self.batch_id,
        }


class TechnicianPaymentBatch(models.Model):
    tenant = models.ForeignKey(
        "users.Organization", on_delete=models.PROTECT,
        related_name="technician_payment_batches", db_index=True,
    )
    technician = models.ForeignKey(
        "users.TenantMembership", on_delete=models.PROTECT,
        related_name="technician_payment_batches", db_index=True,
    )
    payment_date = models.DateField(db_index=True)
    payment_method = models.CharField(
        max_length=20, choices=TechnicianPaymentRecord.PaymentMethod.choices,
    )
    method_description = models.CharField(max_length=120, blank=True, default="")
    reference = models.CharField(max_length=200, blank=True, default="")
    manager_note = models.TextField(blank=True, default="")
    agreed_amount_total_snapshot = models.DecimalField(max_digits=16, decimal_places=2)
    amount_paid_total = models.DecimalField(max_digits=16, decimal_places=2)
    status = models.CharField(
        max_length=24, choices=TechnicianPaymentRecord.Status.choices,
        default=TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION, db_index=True,
    )
    recorded_by = models.ForeignKey(
        "users.TenantMembership", on_delete=models.PROTECT,
        related_name="technician_payment_batches_recorded",
    )
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    disputed_at = models.DateTimeField(null=True, blank=True)
    dispute_reason = models.TextField(blank=True, default="")
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        "users.TenantMembership", on_delete=models.PROTECT,
        related_name="technician_payment_batches_voided", null=True, blank=True,
    )
    void_reason = models.TextField(blank=True, default="")
    replaces = models.OneToOneField(
        "self", on_delete=models.PROTECT, related_name="replacement",
        null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager.from_queryset(ImmutablePaymentQuerySet)()

    class Meta:
        ordering = ["-recorded_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "technician", "status"]),
            models.Index(fields=["tenant", "status", "payment_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(agreed_amount_total_snapshot__gt=0),
                name="tech_payment_batch_agreed_total_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_paid_total__gt=0),
                name="tech_payment_batch_paid_total_positive",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(payment_method="OTHER")
                    | ~models.Q(method_description="")
                ),
                name="tech_payment_batch_other_description",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="CONFIRMED") | models.Q(confirmed_at__isnull=False),
                name="tech_payment_batch_confirmed_at",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="DISPUTED")
                    | (models.Q(disputed_at__isnull=False) & ~models.Q(dispute_reason=""))
                ),
                name="tech_payment_batch_dispute_fields",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="VOIDED")
                    | (
                        models.Q(voided_at__isnull=False)
                        & models.Q(voided_by__isnull=False)
                        & ~models.Q(void_reason="")
                    )
                ),
                name="tech_payment_batch_void_fields",
            ),
        ]

    def __str__(self):
        return f"Technician payment batch {self.pk or 'new'}"

    @property
    def difference(self):
        return self.amount_paid_total - self.agreed_amount_total_snapshot

    def clean(self):
        errors = {}
        if self.agreed_amount_total_snapshot is not None and self.agreed_amount_total_snapshot <= 0:
            errors["agreed_amount_total_snapshot"] = "The approved total must be greater than zero."
        if self.amount_paid_total is not None and self.amount_paid_total <= 0:
            errors["amount_paid_total"] = "The paid total must be greater than zero."
        if self.technician_id and (
            self.technician.tenant_id != self.tenant_id
            or self.technician.base_role != self.technician.BaseRole.TECHNICIAN
        ):
            errors["technician"] = "Batch Technician must belong to this tenant."
        if self.recorded_by_id and self.recorded_by.tenant_id != self.tenant_id:
            errors["recorded_by"] = "Recording Manager belongs to another tenant."
        if self.payment_method == TechnicianPaymentRecord.PaymentMethod.OTHER and not self.method_description.strip():
            errors["method_description"] = "Describe the payment method when Other is selected."
        if self.status == TechnicianPaymentRecord.Status.CONFIRMED and not self.confirmed_at:
            errors["confirmed_at"] = "Confirmed batches require a confirmation timestamp."
        if self.status == TechnicianPaymentRecord.Status.DISPUTED and (
            not self.disputed_at or not self.dispute_reason.strip()
        ):
            errors["dispute_reason"] = "Disputed batches require a timestamp and reason."
        if self.status == TechnicianPaymentRecord.Status.VOIDED and (
            not self.voided_at or not self.voided_by_id or not self.void_reason.strip()
        ):
            errors["void_reason"] = "Voided batches require an actor, timestamp, and reason."
        if self.voided_by_id and self.voided_by.tenant_id != self.tenant_id:
            errors["voided_by"] = "Voiding Manager belongs to another tenant."
        if self.replaces_id and (
            self.replaces_id == self.pk
            or self.replaces.status != TechnicianPaymentRecord.Status.VOIDED
            or self.replaces.tenant_id != self.tenant_id
            or self.replaces.technician_id != self.technician_id
        ):
            errors["replaces"] = "A replacement must reference a voided batch for this Technician."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_lifecycle_transition", False):
            raise ValidationError("Technician payment batches must be changed through lifecycle services.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Technician payment batches are retained as business history and cannot be deleted.")

    def snapshot(self):
        allocations = list(self.allocations.order_by("report_id").values(
            "id", "report_id", "agreed_amount_snapshot", "amount_paid", "adjustment_reason",
        )) if self.pk else []
        for allocation in allocations:
            allocation["payment_record_id"] = allocation.pop("id")
            allocation["agreed_amount_snapshot"] = str(allocation["agreed_amount_snapshot"])
            allocation["amount_paid"] = str(allocation["amount_paid"])
        return {
            "batch_id": self.pk,
            "technician_membership_id": self.technician_id,
            "report_ids": [row["report_id"] for row in allocations],
            "allocations": allocations,
            "payment_date": self.payment_date.isoformat(),
            "payment_method": self.payment_method,
            "method_description": self.method_description,
            "reference": self.reference,
            "manager_note": self.manager_note,
            "agreed_amount_total_snapshot": str(self.agreed_amount_total_snapshot),
            "amount_paid_total": str(self.amount_paid_total),
            "difference": str(self.difference),
            "status": self.status,
            "recorded_by_id": self.recorded_by_id,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "disputed_at": self.disputed_at.isoformat() if self.disputed_at else None,
            "dispute_reason": self.dispute_reason,
            "voided_at": self.voided_at.isoformat() if self.voided_at else None,
            "voided_by_id": self.voided_by_id,
            "void_reason": self.void_reason,
            "replaces_id": self.replaces_id,
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
        PAYMENT_RECORDED = "PAYMENT_RECORDED", "Technician payment recorded"
        PAYMENT_ADJUSTMENT_APPROVED = "PAYMENT_ADJUSTMENT_APPROVED", "Adjusted payment approved"
        PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED", "Payment acknowledgement confirmed"
        PAYMENT_DISPUTED = "PAYMENT_DISPUTED", "Payment disputed"
        PAYMENT_VOIDED = "PAYMENT_VOIDED", "Payment record voided"
        PAYMENT_REPLACED = "PAYMENT_REPLACED", "Payment record replaced"
        PAYMENT_BATCH_RECORDED = "PAYMENT_BATCH_RECORDED", "Technician payment batch recorded"
        PAYMENT_BATCH_CONFIRMED = "PAYMENT_BATCH_CONFIRMED", "Payment batch confirmed"
        PAYMENT_BATCH_DISPUTED = "PAYMENT_BATCH_DISPUTED", "Payment batch disputed"
        PAYMENT_BATCH_VOIDED = "PAYMENT_BATCH_VOIDED", "Payment batch voided"
        PAYMENT_BATCH_REPLACED = "PAYMENT_BATCH_REPLACED", "Payment batch replaced"

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
    event = models.CharField(max_length=32, choices=Event.choices, db_index=True)
    from_status = models.CharField(max_length=24, blank=True, default="")
    to_status = models.CharField(max_length=24, blank=True, default="")
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
