from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from users.models import TenantMembership
from users.permissions import PermissionCode, has_tenant_permission

from .models import TechnicianWorkReport, WorkReportHistory


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


@transaction.atomic
def create_report(*, membership, cleaned_data):
    _require(membership, PermissionCode.TECHNICIAN_WORK_REPORTS_CREATE_OWN)
    if membership.base_role != TenantMembership.BaseRole.TECHNICIAN:
        raise PermissionDenied("Only Technicians create work reports for themselves.")
    report = TechnicianWorkReport(
        tenant=membership.tenant, technician=membership,
        status=TechnicianWorkReport.Status.DRAFT, **cleaned_data,
    )
    report.save()
    _history(report, membership, WorkReportHistory.Event.CREATED)
    return report


@transaction.atomic
def update_own_report(*, report_id, membership, cleaned_data):
    _require(membership, PermissionCode.TECHNICIAN_WORK_REPORTS_UPDATE_OWN)
    report = TechnicianWorkReport.objects.unscoped().select_for_update().filter(
        pk=report_id, tenant=membership.tenant, technician=membership,
    ).first()
    if report is None:
        raise PermissionDenied("Work report is not available.")
    if report.status not in {TechnicianWorkReport.Status.DRAFT, TechnicianWorkReport.Status.REJECTED}:
        raise ValidationError("Only draft or rejected reports may be edited.")
    for field, value in cleaned_data.items():
        setattr(report, field, value)
    report.save()
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
def correct_approved_report(*, report_id, membership, cleaned_data, reason, tenant=None):
    report = _manager_report(report_id, membership, PermissionCode.TECHNICIAN_WORK_REPORTS_CORRECT_APPROVED, tenant)
    if report is None:
        raise PermissionDenied("Work report is not available.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A correction reason is required.")
    if report.status != TechnicianWorkReport.Status.APPROVED:
        raise ValidationError("Only approved reports use the correction workflow.")
    before = report.snapshot()
    for field, value in cleaned_data.items():
        setattr(report, field, value)
    report._approved_correction = True
    report.save()
    WorkReportHistory.objects.create(
        tenant=report.tenant, report=report, actor_membership=membership,
        event=WorkReportHistory.Event.CORRECTED,
        from_status=TechnicianWorkReport.Status.APPROVED,
        to_status=TechnicianWorkReport.Status.APPROVED,
        reason=reason, snapshot={"before": before, "after": report.snapshot()},
    )
    AuditLog.objects.create(
        organization=report.tenant, tenant=report.tenant, actor=membership.user,
        action="technician_work_report.corrected", object_type="TechnicianWorkReport",
        object_id=str(report.pk), old_value=before, new_value=report.snapshot(),
        metadata={"reason": reason},
    )
    return report
