from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Exists, OuterRef, Prefetch, Q, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from users.permissions import PermissionCode, has_tenant_permission, require_permission
from users.tenancy import require_tenant

from .forms import (
    ApprovedCorrectionForm, PaymentDisputeForm, PaymentVoidForm,
    RejectionForm, TechnicianPaymentBatchForm, TechnicianPaymentForm,
    WorkDateFormSet, WorkReportForm,
)
from .models import (
    TechnicianPaymentBatch, TechnicianPaymentRecord, TechnicianWorkReport,
    WorkReportServiceDay,
)
from .policies import (
    pending_approval_queryset_for, technician_payment_batch_queryset_for,
    technician_payment_queryset_for, work_report_queryset_for,
)
from .services import (
    approve_report, correct_approved_report, create_report, reject_report,
    confirm_technician_payment, confirm_technician_payment_batch,
    dispute_technician_payment, dispute_technician_payment_batch,
    record_technician_payment, record_technician_payment_batch,
    replace_technician_payment, submit_report, update_own_report,
    void_technician_payment, void_technician_payment_batch,
)


def _report_or_404(request, pk):
    tenant = require_tenant(request)
    return get_object_or_404(
        work_report_queryset_for(
            request.user, tenant, membership=request.membership,
        ).select_related("technician__user", "customer", "approved_by__user").prefetch_related(
            Prefetch(
                "service_days",
                queryset=WorkReportServiceDay.objects.unscoped().filter(
                    tenant=tenant,
                ).order_by("service_date", "id"),
            ),
        ),
        pk=pk,
    )


def _validation_message(exc):
    return " ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


def _payment_or_404(request, pk):
    tenant = require_tenant(request)
    return get_object_or_404(
        technician_payment_queryset_for(
            request.user, tenant, membership=request.membership,
        ).select_related("report__technician__user", "recorded_by__user", "voided_by__user"),
        pk=pk,
    )


def _request_metadata(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return {
        "ip": (
            forwarded.split(",", 1)[0].strip()
            if forwarded else request.META.get("REMOTE_ADDR", "")
        )[:64],
        "method": request.method,
        "path": request.path[:500],
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
    }


def _work_date_formset(request, *, tenant, report=None):
    if request.method == "POST":
        return WorkDateFormSet(request.POST, prefix="work_dates")
    if report is None:
        initial = [{}]
    else:
        rows = WorkReportServiceDay.objects.unscoped().filter(
            tenant=tenant, report=report,
        ).order_by("service_date", "id")
        initial = [
            {"service_date": row.service_date, "activity_note": row.activity_note}
            for row in rows
        ] or [{"service_date": report.service_date, "activity_note": ""}]
    return WorkDateFormSet(prefix="work_dates", initial=initial)


def _cleaned_work_dates(formset):
    return [
        {
            "service_date": row["service_date"],
            "activity_note": row.get("activity_note", ""),
        }
        for row in formset.cleaned_data
        if not row.get("DELETE") and row.get("service_date")
    ]


@login_required
def report_list(request):
    tenant = require_tenant(request)
    queryset = work_report_queryset_for(
        request.user, tenant, membership=request.membership,
    ).select_related("technician__user", "customer").prefetch_related(
        Prefetch(
            "service_days",
            queryset=WorkReportServiceDay.objects.unscoped().filter(
                tenant=tenant,
            ).order_by("service_date", "id"),
        ),
    )


    active_payment = TechnicianPaymentRecord.objects.unscoped().filter(
        tenant=tenant, report=OuterRef("pk"),
    ).exclude(status=TechnicianPaymentRecord.Status.VOIDED).order_by("-recorded_at")
    queryset = queryset.annotate(
        technician_payment_status=Subquery(active_payment.values("status")[:1]),
    )
    if not (
        has_tenant_permission(request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_OWN, membership=request.membership)
        or has_tenant_permission(request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL, membership=request.membership)
    ):
        raise Http404
    can_review = has_tenant_permission(
        request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
        membership=request.membership,
    )
    status = request.GET.get("status", "").upper()
    if status in TechnicianWorkReport.Status.values:
        queryset = queryset.filter(status=status)
    payment_status = request.GET.get("payment", "").upper()
    if can_review and payment_status == "NOT_RECORDED":
        queryset = queryset.filter(
            status=TechnicianWorkReport.Status.APPROVED,
            technician_payment_status__isnull=True,
        )
    elif can_review and payment_status in TechnicianPaymentRecord.Status.values:
        queryset = queryset.filter(technician_payment_status=payment_status)
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            Q(work_title__icontains=query) | Q(client_name__icontains=query)
            | Q(work_location__icontains=query) | Q(technician__user__username__icontains=query)
        )
    return render(request, "work_reports/report_list.html", {
        "reports": queryset[:200], "selected_status": status, "query": query,
        "can_review": can_review, "selected_payment_status": payment_status,
    })

@login_required
def report_detail(request, pk):
    report = _report_or_404(request, pk)
    tenant = require_tenant(request)
    can_review = has_tenant_permission(
        request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
        membership=request.membership,
    )
    payments = technician_payment_queryset_for(
        request.user, tenant, membership=request.membership,
    ).filter(report=report).select_related("recorded_by__user", "voided_by__user")
    active_payment = payments.exclude(status=TechnicianPaymentRecord.Status.VOIDED).first()
    display_payment = active_payment or payments.first()
    can_record_payment = (
        report.status == TechnicianWorkReport.Status.APPROVED
        and active_payment is None
        and has_tenant_permission(
            request.user, tenant, PermissionCode.TECHNICIAN_PAYMENTS_RECORD,
            membership=request.membership,
        )
        and request.membership.base_role == request.membership.BaseRole.ADMIN_MANAGER
    )
    return render(request, "work_reports/report_detail.html", {
        "report": report, "history": report.history.select_related("actor_membership__user"),
        "can_review": can_review,
        "can_edit": report.technician_id == request.membership.id and report.status in {
            TechnicianWorkReport.Status.DRAFT, TechnicianWorkReport.Status.REJECTED,
        },
        "payment": display_payment,
        "can_record_payment": can_record_payment,
        "can_respond_payment": bool(
            active_payment
            and active_payment.batch_id is None
            and active_payment.status == TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION
            and report.technician_id == request.membership.id
        ),
        "can_void_payment": bool(
            active_payment
            and active_payment.batch_id is None
            and request.membership.base_role == request.membership.BaseRole.ADMIN_MANAGER
            and has_tenant_permission(
                request.user, tenant, PermissionCode.TECHNICIAN_PAYMENTS_VOID,
                membership=request.membership,
            )
        ),
    })


@login_required
def report_create(request):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_WORK_REPORTS_CREATE_OWN)
    form = WorkReportForm(request.POST or None, tenant=tenant)
    work_date_formset = _work_date_formset(request, tenant=tenant)
    if request.method == "POST":
        form_valid = form.is_valid()
        dates_valid = work_date_formset.is_valid()
    else:
        form_valid = dates_valid = False
    if form_valid and dates_valid:
        report = create_report(
            membership=request.membership,
            cleaned_data=form.cleaned_data,
            service_days=_cleaned_work_dates(work_date_formset),
        )
        messages.success(request, "Work report saved as a draft.")
        return redirect("work_reports:detail", pk=report.pk)
    return render(request, "work_reports/report_form.html", {
        "form": form, "work_date_formset": work_date_formset,
        "page_title": "New Work Report",
    })


def _payment_batch_or_404(request, pk):
    tenant = require_tenant(request)
    return get_object_or_404(
        technician_payment_batch_queryset_for(
            request.user, tenant, membership=request.membership,
        ).select_related(
            "technician__user", "recorded_by__user", "voided_by__user", "replaces",
        ).prefetch_related(
            Prefetch(
                "allocations",
                queryset=TechnicianPaymentRecord.objects.unscoped().filter(
                    tenant=tenant,
                ).select_related("report").prefetch_related("report__service_days").order_by("report_id"),
            ),
        ),
        pk=pk,
    )


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
    work_date_formset = _work_date_formset(request, tenant=tenant, report=report)
    if request.method == "POST":
        form_valid = form.is_valid()
        dates_valid = work_date_formset.is_valid()
    else:
        form_valid = dates_valid = False
    if form_valid and dates_valid:
        try:
            report = update_own_report(
                report_id=report.pk, membership=request.membership,
                cleaned_data=form.cleaned_data,
                service_days=_cleaned_work_dates(work_date_formset),
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Work report updated.")
            return redirect("work_reports:detail", pk=report.pk)
    return render(request, "work_reports/report_form.html", {
        "form": form, "work_date_formset": work_date_formset,
        "report": report, "page_title": "Edit Work Report",
    })


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
    ).select_related("technician__user", "customer").prefetch_related(
        Prefetch(
            "service_days",
            queryset=WorkReportServiceDay.objects.unscoped().filter(
                tenant=tenant,
            ).order_by("service_date", "id"),
        ),
    )
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
    work_date_formset = _work_date_formset(request, tenant=tenant, report=report)
    if request.method == "POST":
        form_valid = form.is_valid()
        dates_valid = work_date_formset.is_valid()
    else:
        form_valid = dates_valid = False
    if form_valid and dates_valid:
        cleaned_data = dict(form.cleaned_data)
        reason = cleaned_data.pop("correction_reason")
        try:
            report = correct_approved_report(
                report_id=pk, membership=request.membership,
                cleaned_data=cleaned_data, reason=reason,
                service_days=_cleaned_work_dates(work_date_formset), tenant=tenant,
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Approved report corrected. The previous values remain in history.")
            return redirect("work_reports:detail", pk=report.pk)
    return render(request, "work_reports/report_form.html", {
        "report": report, "form": form, "work_date_formset": work_date_formset,
        "page_title": "Correct Approved Report", "approved_correction": True,
    })


@login_required
def payment_workspace(request):
    tenant = require_tenant(request)
    membership = request.membership
    can_manage = (
        membership.base_role == membership.BaseRole.ADMIN_MANAGER
        and has_tenant_permission(
            request.user, tenant, PermissionCode.TECHNICIAN_PAYMENTS_VIEW_ALL,
            membership=membership,
        )
    )
    can_view_own = (
        membership.base_role == membership.BaseRole.TECHNICIAN
        and has_tenant_permission(
            request.user, tenant, PermissionCode.TECHNICIAN_PAYMENTS_VIEW_OWN,
            membership=membership,
        )
    )
    if not can_manage and not can_view_own:
        raise PermissionDenied("Technician payments are not available.")

    batches = technician_payment_batch_queryset_for(
        request.user, tenant, membership=membership,
    ).select_related("technician__user").prefetch_related("allocations__report__service_days")
    if can_view_own:
        return render(request, "work_reports/payment_workspace.html", {
            "batches": batches[:200], "can_manage": False, "selected_tab": "MY_PAYMENTS",
        })

    selected_tab = request.GET.get("status", "READY").upper()
    valid_tabs = {"READY", *TechnicianPaymentRecord.Status.values}
    if selected_tab not in valid_tabs:
        selected_tab = "READY"
    groups = []
    if selected_tab == "READY":
        active_payment = TechnicianPaymentRecord.objects.unscoped().filter(
            tenant=tenant, report=OuterRef("pk"),
        ).exclude(status=TechnicianPaymentRecord.Status.VOIDED)
        eligible = (
            TechnicianWorkReport.objects.unscoped()
            .filter(
                tenant=tenant,
                status=TechnicianWorkReport.Status.APPROVED,
                technician__tenant=tenant,
                technician__is_active=True,
                technician__base_role=membership.BaseRole.TECHNICIAN,
            )
            .annotate(has_active_payment=Exists(active_payment))
            .filter(has_active_payment=False)
            .select_related("technician__user", "customer")
            .prefetch_related("service_days")
            .order_by("technician__user__first_name", "technician__user__username", "pk")[:200]
        )
        grouped = {}
        for report in eligible:
            group = grouped.setdefault(report.technician_id, {
                "technician": report.technician, "reports": [], "total": Decimal("0"),
            })
            group["reports"].append(report)
            group["total"] += report.agreed_amount
        groups = list(grouped.values())
    else:
        batches = batches.filter(status=selected_tab)
    return render(request, "work_reports/payment_workspace.html", {
        "batches": batches[:200], "groups": groups, "can_manage": True,
        "selected_tab": selected_tab,
    })


def _selected_batch_reports(request, *, tenant, report_ids):
    normalized = []
    for raw_id in report_ids:
        try:
            value = int(raw_id)
        except (TypeError, ValueError):
            raise Http404
        if value not in normalized:
            normalized.append(value)
    if not normalized or len(normalized) > 100:
        raise ValidationError("Select between 1 and 100 Work Reports.")
    reports = list(
        work_report_queryset_for(
            request.user, tenant, membership=request.membership,
        ).filter(pk__in=normalized).select_related("technician__user", "customer")
        .prefetch_related("service_days").order_by("pk")
    )
    if len(reports) != len(normalized):
        raise Http404
    if len({report.technician_id for report in reports}) != 1:
        raise ValidationError("Select Work Reports for one Technician only.")
    technician = reports[0].technician
    if (
        not technician.is_active
        or technician.tenant_id != tenant.id
        or technician.base_role != technician.BaseRole.TECHNICIAN
    ):
        raise ValidationError("The selected Technician is not active in this tenant.")
    if any(report.status != TechnicianWorkReport.Status.APPROVED for report in reports):
        raise ValidationError("Select only approved Work Reports.")
    if TechnicianPaymentRecord.objects.unscoped().filter(
        tenant=tenant, report_id__in=normalized,
    ).exclude(status=TechnicianPaymentRecord.Status.VOIDED).exists():
        raise ValidationError("One or more selected Work Reports already has an active payment.")
    return reports


def _batch_form_rows(form, reports):
    return [
        {
            "report": report,
            "amount": form[f"allocation_{report.pk}_amount_paid"],
            "reason": form[f"allocation_{report.pk}_adjustment_reason"],
            "confirmation": form[f"allocation_{report.pk}_confirm_adjusted_amount"],
        }
        for report in reports
    ]


@login_required
def payment_batch_record(request):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_PAYMENTS_RECORD)
    if request.membership.base_role != request.membership.BaseRole.ADMIN_MANAGER:
        raise PermissionDenied("Only an Administrator / Manager may record Technician payments.")
    if request.method != "POST":
        return redirect("work_reports:payment_workspace")
    report_ids = request.POST.getlist("report_ids")
    try:
        reports = _selected_batch_reports(request, tenant=tenant, report_ids=report_ids)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        return redirect("work_reports:payment_workspace")
    is_record_submission = request.POST.get("action") == "record"
    form = TechnicianPaymentBatchForm(
        request.POST if is_record_submission else None, reports=reports,
    )
    if is_record_submission and form.is_valid():
        data = form.cleaned_data
        try:
            batch = record_technician_payment_batch(
                report_allocations=form.allocations(),
                membership=request.membership,
                payment_date=data["payment_date"],
                payment_method=data["payment_method"],
                method_description=data["method_description"],
                reference=data["reference"], manager_note=data["manager_note"],
                tenant=tenant, request_metadata=_request_metadata(request),
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(
                request,
                f"Payment batch recorded for {batch.technician.user.get_full_name() or batch.technician.user.username}. Awaiting Technician confirmation.",
            )
            return redirect("work_reports:payment_batch_detail", pk=batch.pk)
    return render(request, "work_reports/payment_batch_form.html", {
        "form": form, "reports": reports, "allocation_rows": _batch_form_rows(form, reports),
        "technician": reports[0].technician,
    })


@login_required
def payment_batch_detail(request, pk):
    batch = _payment_batch_or_404(request, pk)
    tenant = require_tenant(request)
    is_owner = batch.technician_id == request.membership.id
    can_manage = (
        request.membership.base_role == request.membership.BaseRole.ADMIN_MANAGER
        and has_tenant_permission(
            request.user, tenant, PermissionCode.TECHNICIAN_PAYMENTS_VIEW_ALL,
            membership=request.membership,
        )
    )
    return render(request, "work_reports/payment_batch_detail.html", {
        "batch": batch,
        "can_respond": is_owner and batch.status == TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION,
        "can_void": can_manage and batch.status != TechnicianPaymentRecord.Status.VOIDED,
        "can_replace": can_manage and batch.status == TechnicianPaymentRecord.Status.VOIDED
            and not hasattr(batch, "replacement"),
    })


@login_required
@require_POST
def payment_batch_confirm(request, pk):
    tenant = require_tenant(request)
    batch = _payment_batch_or_404(request, pk)
    try:
        batch = confirm_technician_payment_batch(
            batch_id=batch.pk, membership=request.membership, tenant=tenant,
            request_metadata=_request_metadata(request),
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "The complete payment batch has been acknowledged as received.")
    return redirect("work_reports:payment_batch_detail", pk=batch.pk)


@login_required
def payment_batch_dispute(request, pk):
    tenant = require_tenant(request)
    batch = _payment_batch_or_404(request, pk)
    if batch.technician_id != request.membership.id or batch.status != TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION:
        raise Http404
    form = PaymentDisputeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            batch = dispute_technician_payment_batch(
                batch_id=batch.pk, membership=request.membership,
                reason=form.cleaned_data["reason"], tenant=tenant,
                request_metadata=_request_metadata(request),
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Payment batch disputed. The Manager's record remains in history.")
            return redirect("work_reports:payment_batch_detail", pk=batch.pk)
    return render(request, "work_reports/payment_batch_action.html", {
        "batch": batch, "form": form, "action": "dispute",
    })


@login_required
def payment_batch_void(request, pk):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_PAYMENTS_VOID)
    if request.membership.base_role != request.membership.BaseRole.ADMIN_MANAGER:
        raise PermissionDenied("Only an Administrator / Manager may void Technician payments.")
    batch = _payment_batch_or_404(request, pk)
    if batch.status == TechnicianPaymentRecord.Status.VOIDED:
        raise Http404
    form = PaymentVoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            batch = void_technician_payment_batch(
                batch_id=batch.pk, membership=request.membership,
                reason=form.cleaned_data["reason"], tenant=tenant,
                request_metadata=_request_metadata(request),
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Payment batch voided. Its allocations and response remain in history.")
            return redirect("work_reports:payment_batch_detail", pk=batch.pk)
    return render(request, "work_reports/payment_batch_action.html", {
        "batch": batch, "form": form, "action": "void",
    })


@login_required
def payment_batch_replace(request, pk):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_PAYMENTS_RECORD)
    if request.membership.base_role != request.membership.BaseRole.ADMIN_MANAGER:
        raise PermissionDenied("Only an Administrator / Manager may replace Technician payments.")
    batch = _payment_batch_or_404(request, pk)
    if batch.status != TechnicianPaymentRecord.Status.VOIDED or hasattr(batch, "replacement"):
        raise Http404
    reports = [allocation.report for allocation in batch.allocations.all()]
    form = TechnicianPaymentBatchForm(request.POST or None, reports=reports)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            replacement = record_technician_payment_batch(
                report_allocations=form.allocations(), membership=request.membership,
                payment_date=data["payment_date"], payment_method=data["payment_method"],
                method_description=data["method_description"], reference=data["reference"],
                manager_note=data["manager_note"], tenant=tenant, replaces_batch_id=batch.pk,
                request_metadata=_request_metadata(request),
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Replacement payment batch recorded and awaiting confirmation.")
            return redirect("work_reports:payment_batch_detail", pk=replacement.pk)
    return render(request, "work_reports/payment_batch_form.html", {
        "form": form, "reports": reports, "allocation_rows": _batch_form_rows(form, reports),
        "technician": batch.technician,
        "replaces": batch,
    })


def _render_payment_form(request, *, report, replaces=None):
    form = TechnicianPaymentForm(request.POST or None, report=report)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        service_kwargs = {
            "membership": request.membership,
            "amount_paid": data["amount_paid"],
            "payment_date": data["payment_date"],
            "payment_method": data["payment_method"],
            "reference": data["reference"],
            "manager_note": data["manager_note"],
            "adjustment_reason": data["adjustment_reason"],
            "confirm_adjusted_amount": data["confirm_adjusted_amount"],
            "request_metadata": _request_metadata(request),
        }
        try:
            if replaces is None:
                payment = record_technician_payment(
                    report_id=report.pk, tenant=require_tenant(request), **service_kwargs,
                )
            else:
                payment = replace_technician_payment(
                    voided_payment_id=replaces.pk, **service_kwargs,
                )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(
                request,
                "Technician payment recorded. It is awaiting the Technician's confirmation.",
            )
            return redirect("work_reports:detail", pk=payment.report_id)
    return render(request, "work_reports/payment_form.html", {
        "form": form, "report": report, "replaces": replaces,
    })


@login_required
def payment_record(request, report_pk):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_PAYMENTS_RECORD)
    report = get_object_or_404(
        work_report_queryset_for(
            request.user, tenant, membership=request.membership,
        ).select_related("technician__user"),
        pk=report_pk, status=TechnicianWorkReport.Status.APPROVED,
    )
    return _render_payment_form(request, report=report)


@login_required
def payment_replace(request, pk):
    require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_PAYMENTS_RECORD)
    payment = _payment_or_404(request, pk)
    if payment.status != TechnicianPaymentRecord.Status.VOIDED:
        raise Http404
    return _render_payment_form(request, report=payment.report, replaces=payment)


@login_required
@require_POST
def payment_confirm(request, pk):
    tenant = require_tenant(request)
    payment = _payment_or_404(request, pk)
    if payment.batch_id:
        messages.info(request, "Confirm the complete payment batch, not an individual allocation.")
        return redirect("work_reports:payment_batch_detail", pk=payment.batch_id)
    try:
        payment = confirm_technician_payment(
            payment_id=payment.pk, membership=request.membership, tenant=tenant,
            request_metadata=_request_metadata(request),
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "The recorded amount has been acknowledged as received.")
    return redirect("work_reports:detail", pk=payment.report_id)


@login_required
def payment_dispute(request, pk):
    tenant = require_tenant(request)
    payment = _payment_or_404(request, pk)
    if payment.batch_id:
        return redirect("work_reports:payment_batch_dispute", pk=payment.batch_id)
    if (
        payment.report.technician_id != request.membership.id
        or payment.status != TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION
    ):
        raise Http404
    form = PaymentDisputeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            payment = dispute_technician_payment(
                payment_id=payment.pk, membership=request.membership,
                reason=form.cleaned_data["reason"], tenant=tenant,
                request_metadata=_request_metadata(request),
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Payment disputed. The Manager's record remains in history.")
            return redirect("work_reports:detail", pk=payment.report_id)
    return render(request, "work_reports/payment_dispute.html", {
        "payment": payment, "report": payment.report, "form": form,
    })


@login_required
def payment_void(request, pk):
    tenant = require_tenant(request)
    require_permission(request, PermissionCode.TECHNICIAN_PAYMENTS_VOID)
    payment = _payment_or_404(request, pk)
    if payment.batch_id:
        return redirect("work_reports:payment_batch_void", pk=payment.batch_id)
    if payment.status == TechnicianPaymentRecord.Status.VOIDED:
        raise Http404
    form = PaymentVoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            payment = void_technician_payment(
                payment_id=payment.pk, membership=request.membership,
                reason=form.cleaned_data["reason"], tenant=tenant,
                request_metadata=_request_metadata(request),
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Payment record voided. You may record a linked replacement.")
            return redirect("work_reports:detail", pk=payment.report_id)
    return render(request, "work_reports/payment_void.html", {
        "payment": payment, "report": payment.report, "form": form,
    })
