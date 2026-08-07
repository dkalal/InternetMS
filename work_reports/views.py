from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from users.permissions import PermissionCode, has_tenant_permission, require_permission
from users.tenancy import require_tenant

from .forms import ApprovedCorrectionForm, RejectionForm, WorkReportForm
from .models import TechnicianWorkReport
from .policies import pending_approval_queryset_for, work_report_queryset_for
from .services import (
    approve_report, correct_approved_report, create_report, reject_report,
    submit_report, update_own_report,
)


def _report_or_404(request, pk):
    tenant = require_tenant(request)
    return get_object_or_404(
        work_report_queryset_for(
            request.user, tenant, membership=request.membership,
        ).select_related("technician__user", "customer", "approved_by__user"),
        pk=pk,
    )


def _validation_message(exc):
    return " ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


@login_required
def report_list(request):
    tenant = require_tenant(request)
    queryset = work_report_queryset_for(
        request.user, tenant, membership=request.membership,
    ).select_related("technician__user", "customer")
    if not (
        has_tenant_permission(request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_OWN, membership=request.membership)
        or has_tenant_permission(request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL, membership=request.membership)
    ):
        raise Http404
    status = request.GET.get("status", "").upper()
    if status in TechnicianWorkReport.Status.values:
        queryset = queryset.filter(status=status)
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            Q(work_title__icontains=query) | Q(client_name__icontains=query)
            | Q(work_location__icontains=query) | Q(technician__user__username__icontains=query)
        )
    can_review = has_tenant_permission(
        request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
        membership=request.membership,
    )
    return render(request, "work_reports/report_list.html", {
        "reports": queryset[:200], "selected_status": status, "query": query,
        "can_review": can_review,
    })

@login_required
def report_detail(request, pk):
    report = _report_or_404(request, pk)
    tenant = require_tenant(request)
    can_review = has_tenant_permission(
        request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
        membership=request.membership,
    )
    return render(request, "work_reports/report_detail.html", {
        "report": report, "history": report.history.select_related("actor_membership__user"),
        "can_review": can_review,
        "can_edit": report.technician_id == request.membership.id and report.status in {
            TechnicianWorkReport.Status.DRAFT, TechnicianWorkReport.Status.REJECTED,
        },
    })


@login_required
def report_create(request):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_WORK_REPORTS_CREATE_OWN)
    form = WorkReportForm(request.POST or None, tenant=tenant)
    if request.method == "POST" and form.is_valid():
        report = create_report(membership=request.membership, cleaned_data=form.cleaned_data)
        messages.success(request, "Work report saved as a draft.")
        return redirect("work_reports:detail", pk=report.pk)
    return render(request, "work_reports/report_form.html", {"form": form, "page_title": "New Work Report"})


@login_required
def report_edit(request, pk):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_WORK_REPORTS_UPDATE_OWN)
    report = get_object_or_404(
        TechnicianWorkReport.objects.unscoped(), pk=pk, tenant=tenant,
        technician=request.membership,
    )
    if report.status not in {TechnicianWorkReport.Status.DRAFT, TechnicianWorkReport.Status.REJECTED}:
        raise Http404
    form = WorkReportForm(request.POST or None, instance=report, tenant=tenant)
    if request.method == "POST" and form.is_valid():
        try:
            report = update_own_report(
                report_id=report.pk, membership=request.membership, cleaned_data=form.cleaned_data,
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Work report updated.")
            return redirect("work_reports:detail", pk=report.pk)
    return render(request, "work_reports/report_form.html", {"form": form, "report": report, "page_title": "Edit Work Report"})


@login_required
@require_POST
def report_submit(request, pk):
    require_tenant(request)
    try:
        report = submit_report(report_id=pk, membership=request.membership)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        return redirect("work_reports:detail", pk=pk)
    messages.success(request, "Work report submitted for review.")
    return redirect("work_reports:detail", pk=report.pk)


@login_required
def approval_queue(request):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL)
    reports = pending_approval_queryset_for(
        request.user, tenant, membership=request.membership,
    ).select_related("technician__user", "customer")
    query = request.GET.get("q", "").strip()
    if query:
        reports = reports.filter(
            Q(work_title__icontains=query) | Q(client_name__icontains=query)
            | Q(technician__user__username__icontains=query)
        )
    return render(request, "work_reports/approval_queue.html", {"reports": reports[:200], "query": query})


@login_required
@require_POST
def report_approve(request, pk):
    tenant = require_tenant(request)
    try:
        report = approve_report(report_id=pk, membership=request.membership, tenant=tenant)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        return redirect("work_reports:detail", pk=pk)
    messages.success(request, "Work report approved and locked.")
    return redirect("work_reports:detail", pk=report.pk)


@login_required
def report_reject(request, pk):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_WORK_REPORTS_REJECT)
    report = get_object_or_404(
        work_report_queryset_for(request.user, tenant, membership=request.membership),
        pk=pk, status=TechnicianWorkReport.Status.SUBMITTED,
    )
    form = RejectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            report = reject_report(
                report_id=pk, membership=request.membership,
                reason=form.cleaned_data["reason"], tenant=tenant,
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Report returned to the Technician with a correction reason.")
            return redirect("work_reports:detail", pk=report.pk)
    return render(request, "work_reports/reject_form.html", {"report": report, "form": form})


@login_required
def report_correct(request, pk):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_WORK_REPORTS_CORRECT_APPROVED)
    report = get_object_or_404(
        work_report_queryset_for(request.user, tenant, membership=request.membership),
        pk=pk, status=TechnicianWorkReport.Status.APPROVED,
    )
    form = ApprovedCorrectionForm(request.POST or None, instance=report, tenant=tenant)
    if request.method == "POST" and form.is_valid():
        cleaned_data = dict(form.cleaned_data)
        reason = cleaned_data.pop("correction_reason")
        try:
            report = correct_approved_report(
                report_id=pk, membership=request.membership,
                cleaned_data=cleaned_data, reason=reason, tenant=tenant,
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Approved report corrected. The previous values remain in history.")
            return redirect("work_reports:detail", pk=report.pk)
    return render(request, "work_reports/report_form.html", {
        "report": report, "form": form, "page_title": "Correct Approved Report", "approved_correction": True,
    })
