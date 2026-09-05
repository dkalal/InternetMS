from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditLog
from users.models import TenantMembership
from users.permissions import PermissionCode, has_tenant_permission

from .models import (
    TechnicianPaymentRecord, TechnicianWorkReport, WorkReportHistory,
    WorkReportServiceDay,
)


def _require(membership, action, *, tenant=None):
    tenant = tenant or (membership.tenant if membership else None)
    if membership is None or not membership.is_active or not has_tenant_permission(
        membership.user, tenant, action, membership=membership,
    ):
        raise PermissionDenied("Insufficient work report permissions.")


def _history(report, membership, event, *, from_status="", reason=""):
    WorkReportHistory.objects.create(
        tenant=report.tenant, report=report, actor_membership=membership,
        event=event, from_status=from_status, to_status=report.status,
        reason=reason, snapshot=report.snapshot(),
    )
    AuditLog.objects.create(
        organization=report.tenant, tenant=report.tenant, actor=membership.user,
        action=f"technician_work_report.{event.lower()}",
        object_type="TechnicianWorkReport", object_id=str(report.pk),
        old_value={"status": from_status} if from_status else {},
        new_value={"status": report.status},
        metadata={"history_event": event, "reason": reason},
    )


def _validated_service_days(service_days):
    """Validate authoritative work dates again at the trusted write boundary."""
    if not service_days:
        raise ValidationError("Add at least one work date.")
    normalized = []
    seen = set()
    today = timezone.localdate()
    for row in service_days:
        service_date = row.get("service_date")
        note = (row.get("activity_note") or "").strip()
        if not service_date:
            raise ValidationError("Every work-date row requires a date.")
        if service_date > today:
            raise ValidationError("Work dates cannot be in the future.")
        if service_date in seen:
            raise ValidationError("The same work date cannot appear twice.")
        if len(note) > 500:
            raise ValidationError("A daily work note cannot exceed 500 characters.")
        seen.add(service_date)
        normalized.append({"service_date": service_date, "activity_note": note})
    return sorted(normalized, key=lambda row: row["service_date"])


def _service_days_from_legacy(cleaned_data, service_days):
    """Keep service callers compatible while browser forms use only the formset."""
    cleaned_data = dict(cleaned_data)
    legacy_date = cleaned_data.pop("service_date", None)
    if service_days is None and legacy_date is not None:
        service_days = [{"service_date": legacy_date, "activity_note": ""}]
    return cleaned_data, service_days


def _replace_service_days(*, report, service_days, approved_correction=False):
    rows = _validated_service_days(service_days)
    existing = WorkReportServiceDay.objects.unscoped().select_for_update().filter(
        tenant=report.tenant, report=report,
    )
    for service_day in existing:
        service_day._service_write = True
        service_day.delete()
    for row in rows:
        service_day = WorkReportServiceDay(
            tenant=report.tenant,
            report=report,
            service_date=row["service_date"],
            activity_note=row["activity_note"],
        )
        service_day._service_write = approved_correction
        service_day.save()
    return rows


@transaction.atomic
def create_report(*, membership, cleaned_data, service_days=None):
    _require(membership, PermissionCode.TECHNICIAN_WORK_REPORTS_CREATE_OWN)
    if membership.base_role != TenantMembership.BaseRole.TECHNICIAN:
        raise PermissionDenied("Only Technicians create work reports for themselves.")
    cleaned_data, service_days = _service_days_from_legacy(cleaned_data, service_days)
    rows = _validated_service_days(service_days)
    report = TechnicianWorkReport(
        tenant=membership.tenant, technician=membership,
        status=TechnicianWorkReport.Status.DRAFT,
        service_date=rows[0]["service_date"],
        **cleaned_data,
    )
    report.save()
    _replace_service_days(report=report, service_days=rows)
    _history(report, membership, WorkReportHistory.Event.CREATED)
    return report


@transaction.atomic
def update_own_report(*, report_id, membership, cleaned_data, service_days=None):
    _require(membership, PermissionCode.TECHNICIAN_WORK_REPORTS_UPDATE_OWN)
    report = TechnicianWorkReport.objects.unscoped().select_for_update().filter(
        pk=report_id, tenant=membership.tenant, technician=membership,
    ).first()
    if report is None:
        raise PermissionDenied("Work report is not available.")
    if report.status not in {TechnicianWorkReport.Status.DRAFT, TechnicianWorkReport.Status.REJECTED}:
        raise ValidationError("Only draft or rejected reports may be edited.")
    cleaned_data, service_days = _service_days_from_legacy(cleaned_data, service_days)
    rows = _validated_service_days(service_days) if service_days is not None else None
    for field, value in cleaned_data.items():
        setattr(report, field, value)
    if rows is not None:
        report.service_date = rows[0]["service_date"]
    report.save()
    if rows is not None:
        _replace_service_days(report=report, service_days=rows)
    _history(report, membership, WorkReportHistory.Event.UPDATED, from_status=report.status)
    return report


@transaction.atomic
def submit_report(*, report_id, membership):
    _require(membership, PermissionCode.TECHNICIAN_WORK_REPORTS_SUBMIT_OWN)
    report = TechnicianWorkReport.objects.unscoped().select_for_update().filter(
        pk=report_id, tenant=membership.tenant, technician=membership,
    ).first()
    if report is None:
        raise PermissionDenied("Work report is not available.")
    if report.status not in {TechnicianWorkReport.Status.DRAFT, TechnicianWorkReport.Status.REJECTED}:
        raise ValidationError("This report cannot be submitted in its current state.")
    if not WorkReportServiceDay.objects.unscoped().filter(
        tenant=report.tenant, report=report,
    ).exists():
        raise ValidationError("Add at least one work date before submitting.")
    old_status = report.status
    report.status = TechnicianWorkReport.Status.SUBMITTED
    report.submitted_at = timezone.now()
    report.rejection_reason = ""
    report.save()
    event = WorkReportHistory.Event.RESUBMITTED if old_status == TechnicianWorkReport.Status.REJECTED else WorkReportHistory.Event.SUBMITTED
    _history(report, membership, event, from_status=old_status)
    return report


def _manager_report(report_id, membership, permission, tenant=None):
    tenant = tenant or membership.tenant
    _require(membership, permission, tenant=tenant)
    return TechnicianWorkReport.objects.unscoped().select_for_update().filter(
        pk=report_id, tenant=tenant,
    ).first()


@transaction.atomic
def approve_report(*, report_id, membership, tenant=None):
    report = _manager_report(report_id, membership, PermissionCode.TECHNICIAN_WORK_REPORTS_APPROVE, tenant)
    if report is None:
        raise PermissionDenied("Work report is not available.")
    if report.status != TechnicianWorkReport.Status.SUBMITTED:
        raise ValidationError("Only submitted reports may be approved.")
    old_status = report.status
    report.status = TechnicianWorkReport.Status.APPROVED
    report.approved_by = membership
    report.approved_at = timezone.now()
    report.rejection_reason = ""
    report.save()
    _history(report, membership, WorkReportHistory.Event.APPROVED, from_status=old_status)
    return report


@transaction.atomic
def reject_report(*, report_id, membership, reason, tenant=None):
    report = _manager_report(report_id, membership, PermissionCode.TECHNICIAN_WORK_REPORTS_REJECT, tenant)
    if report is None:
        raise PermissionDenied("Work report is not available.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A rejection reason is required.")
    if report.status != TechnicianWorkReport.Status.SUBMITTED:
        raise ValidationError("Only submitted reports may be rejected.")
    old_status = report.status
    report.status = TechnicianWorkReport.Status.REJECTED
    report.rejection_reason = reason
    report.approved_by = None
    report.approved_at = None
    report.save()
    _history(report, membership, WorkReportHistory.Event.REJECTED, from_status=old_status, reason=reason)
    return report


@transaction.atomic
def correct_approved_report(
    *, report_id, membership, cleaned_data, reason, service_days=None, tenant=None,
):
    report = _manager_report(report_id, membership, PermissionCode.TECHNICIAN_WORK_REPORTS_CORRECT_APPROVED, tenant)
    if report is None:
        raise PermissionDenied("Work report is not available.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A correction reason is required.")
    if report.status != TechnicianWorkReport.Status.APPROVED:
        raise ValidationError("Only approved reports use the correction workflow.")
    before = report.snapshot()
    cleaned_data, service_days = _service_days_from_legacy(cleaned_data, service_days)
    rows = _validated_service_days(service_days) if service_days is not None else None
    if (
        "agreed_amount" in cleaned_data
        and cleaned_data["agreed_amount"] != report.agreed_amount
        and TechnicianPaymentRecord.objects.unscoped().filter(report=report).exists()
    ):
        raise ValidationError(
            "The agreed amount cannot be corrected after payment history has been recorded."
        )
    for field, value in cleaned_data.items():
        setattr(report, field, value)
    if rows is not None:
        report.service_date = rows[0]["service_date"]
    report._approved_correction = True
    report.save()
    if rows is not None:
        _replace_service_days(
            report=report, service_days=rows, approved_correction=True,
        )
    after = report.snapshot()
    WorkReportHistory.objects.create(
        tenant=report.tenant, report=report, actor_membership=membership,
        event=WorkReportHistory.Event.CORRECTED,
        from_status=TechnicianWorkReport.Status.APPROVED,
        to_status=TechnicianWorkReport.Status.APPROVED,
        reason=reason, snapshot={"before": before, "after": after},
    )
    AuditLog.objects.create(
        organization=report.tenant, tenant=report.tenant, actor=membership.user,
        action="technician_work_report.corrected", object_type="TechnicianWorkReport",
        object_id=str(report.pk), old_value=before, new_value=after,
        metadata={"reason": reason},
    )
    return report


def _require_payment_manager(membership, permission, *, tenant):
    _require(membership, permission, tenant=tenant)
    if membership.base_role != TenantMembership.BaseRole.ADMIN_MANAGER:
        raise PermissionDenied("Only an Administrator / Manager may manage Technician payments.")
    if membership.tenant_id != tenant.id or not tenant.is_active:
        raise PermissionDenied("Technician payment is not available in this tenant.")


def _require_payment_technician(membership, permission, *, tenant):
    _require(membership, permission, tenant=tenant)
    if (
        membership.base_role != TenantMembership.BaseRole.TECHNICIAN
        or membership.tenant_id != tenant.id
        or not tenant.is_active
    ):
        raise PermissionDenied("Technician payment is not available.")


def _payment_history(payment, membership, event, *, from_status="", reason="", request_metadata=None):
    snapshot = payment.snapshot()
    WorkReportHistory.objects.create(
        tenant=payment.tenant,
        report=payment.report,
        actor_membership=membership,
        event=event,
        from_status=from_status,
        to_status=payment.status,
        reason=reason,
        snapshot=snapshot,
    )
    metadata = {
        "history_event": event,
        "report_id": payment.report_id,
        "payment_record_id": payment.pk,
    }
    metadata.update(request_metadata or {})
    AuditLog.objects.create(
        organization=payment.tenant,
        tenant=payment.tenant,
        actor=membership.user,
        action=f"technician_payment.{event.lower()}",
        object_type="TechnicianPaymentRecord",
        object_id=str(payment.pk),
        old_value={"status": from_status} if from_status else {},
        new_value=snapshot,
        metadata=metadata,
    )


@transaction.atomic
def record_technician_payment(
    *, report_id, membership, amount_paid, payment_date, payment_method,
    reference="", manager_note="", adjustment_reason="",
    confirm_adjusted_amount=False, replaces_id=None, tenant=None,
    request_metadata=None,
):
    tenant = tenant or membership.tenant
    _require_payment_manager(membership, PermissionCode.TECHNICIAN_PAYMENTS_RECORD, tenant=tenant)
    report = TechnicianWorkReport.objects.unscoped().select_for_update().select_related(
        "technician"
    ).filter(pk=report_id, tenant=tenant).first()
    if report is None:
        raise PermissionDenied("Work report is not available.")
    if report.status != TechnicianWorkReport.Status.APPROVED:
        raise ValidationError("Only an approved Work Report can have a payment record.")
    if (
        not report.technician.is_active
        or report.technician.tenant_id != tenant.id
        or report.technician.base_role != TenantMembership.BaseRole.TECHNICIAN
    ):
        raise ValidationError("The report Technician membership is not active in this tenant.")
    if TechnicianPaymentRecord.objects.unscoped().filter(
        tenant=tenant, report=report,
    ).exclude(status=TechnicianPaymentRecord.Status.VOIDED).exists():
        raise ValidationError("This Work Report already has an active payment record.")

    replacement = None
    if replaces_id is not None:
        replacement = TechnicianPaymentRecord.objects.unscoped().select_for_update().filter(
            pk=replaces_id, tenant=tenant, report=report,
        ).first()
        if replacement is None or replacement.status != TechnicianPaymentRecord.Status.VOIDED:
            raise ValidationError("A replacement must reference a voided payment for this Work Report.")
        if TechnicianPaymentRecord.objects.unscoped().filter(replaces=replacement).exists():
            raise ValidationError("This voided payment has already been replaced.")

    adjustment_reason = (adjustment_reason or "").strip()
    amount_differs = amount_paid != report.agreed_amount
    if amount_differs and not adjustment_reason:
        raise ValidationError("An adjustment reason is required when the paid amount differs.")
    if amount_differs and confirm_adjusted_amount is not True:
        raise ValidationError("Explicit confirmation of the adjusted final amount is required.")

    payment = TechnicianPaymentRecord(
        tenant=tenant,
        report=report,
        agreed_amount_snapshot=report.agreed_amount,
        amount_paid=amount_paid,
        payment_date=payment_date,
        payment_method=payment_method,
        reference=(reference or "").strip(),
        manager_note=(manager_note or "").strip(),
        adjustment_reason=adjustment_reason,
        status=TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION,
        recorded_by=membership,
        replaces=replacement,
    )
    try:
        with transaction.atomic():
            payment.save()
    except IntegrityError as exc:
        raise ValidationError("This Work Report already has an active payment record.") from exc

    initial_status = TechnicianPaymentRecord.Status.VOIDED if replacement else "NOT_RECORDED"
    _payment_history(
        payment, membership, WorkReportHistory.Event.PAYMENT_RECORDED,
        from_status=initial_status, request_metadata=request_metadata,
    )
    if amount_differs:
        _payment_history(
            payment, membership, WorkReportHistory.Event.PAYMENT_ADJUSTMENT_APPROVED,
            from_status=payment.status, reason=adjustment_reason,
            request_metadata=request_metadata,
        )
    if replacement:
        _payment_history(
            payment, membership, WorkReportHistory.Event.PAYMENT_REPLACED,
            from_status=TechnicianPaymentRecord.Status.VOIDED,
            reason=replacement.void_reason, request_metadata=request_metadata,
        )
    return payment


@transaction.atomic
def confirm_technician_payment(*, payment_id, membership, tenant=None, request_metadata=None):
    tenant = tenant or membership.tenant
    _require_payment_technician(
        membership, PermissionCode.TECHNICIAN_PAYMENTS_CONFIRM_OWN, tenant=tenant,
    )
    payment = TechnicianPaymentRecord.objects.unscoped().select_for_update().select_related(
        "report__technician"
    ).filter(pk=payment_id, tenant=tenant, report__technician=membership).first()
    if payment is None:
        raise PermissionDenied("Technician payment is not available.")
    if payment.status != TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION:
        raise ValidationError("This payment record can no longer be confirmed.")
    previous_status = payment.status
    payment.status = TechnicianPaymentRecord.Status.CONFIRMED
    payment.confirmed_at = timezone.now()
    payment._lifecycle_transition = True
    payment.save(update_fields=["status", "confirmed_at", "updated_at"])
    _payment_history(
        payment, membership, WorkReportHistory.Event.PAYMENT_CONFIRMED,
        from_status=previous_status, request_metadata=request_metadata,
    )
    return payment


@transaction.atomic
def dispute_technician_payment(
    *, payment_id, membership, reason, tenant=None, request_metadata=None,
):
    tenant = tenant or membership.tenant
    _require_payment_technician(
        membership, PermissionCode.TECHNICIAN_PAYMENTS_DISPUTE_OWN, tenant=tenant,
    )
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Please provide a meaningful dispute reason.")
    payment = TechnicianPaymentRecord.objects.unscoped().select_for_update().select_related(
        "report__technician"
    ).filter(pk=payment_id, tenant=tenant, report__technician=membership).first()
    if payment is None:
        raise PermissionDenied("Technician payment is not available.")
    if payment.status != TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION:
        raise ValidationError("This payment record can no longer be disputed.")
    previous_status = payment.status
    payment.status = TechnicianPaymentRecord.Status.DISPUTED
    payment.disputed_at = timezone.now()
    payment.dispute_reason = reason
    payment._lifecycle_transition = True
    payment.save(update_fields=["status", "disputed_at", "dispute_reason", "updated_at"])
    _payment_history(
        payment, membership, WorkReportHistory.Event.PAYMENT_DISPUTED,
        from_status=previous_status, reason=reason, request_metadata=request_metadata,
    )
    return payment


@transaction.atomic
def void_technician_payment(
    *, payment_id, membership, reason, tenant=None, request_metadata=None,
):
    tenant = tenant or membership.tenant
    _require_payment_manager(membership, PermissionCode.TECHNICIAN_PAYMENTS_VOID, tenant=tenant)
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("A meaningful void reason is required.")
    payment = TechnicianPaymentRecord.objects.unscoped().select_for_update().select_related(
        "report__technician"
    ).filter(pk=payment_id, tenant=tenant).first()
    if payment is None:
        raise PermissionDenied("Technician payment is not available.")
    if payment.status == TechnicianPaymentRecord.Status.VOIDED:
        raise ValidationError("This payment record is already voided.")
    previous_status = payment.status
    payment.status = TechnicianPaymentRecord.Status.VOIDED
    payment.voided_at = timezone.now()
    payment.voided_by = membership
    payment.void_reason = reason
    payment._lifecycle_transition = True
    payment.save(update_fields=[
        "status", "voided_at", "voided_by", "void_reason", "updated_at",
    ])
    _payment_history(
        payment, membership, WorkReportHistory.Event.PAYMENT_VOIDED,
        from_status=previous_status, reason=reason, request_metadata=request_metadata,
    )
    return payment


def replace_technician_payment(*, voided_payment_id, membership, **payment_data):
    voided = TechnicianPaymentRecord.objects.unscoped().filter(
        pk=voided_payment_id, tenant=membership.tenant,
    ).only("report_id").first()
    if voided is None:
        raise PermissionDenied("Technician payment is not available.")
    return record_technician_payment(
        report_id=voided.report_id,
        membership=membership,
        replaces_id=voided_payment_id,
        **payment_data,
    )
