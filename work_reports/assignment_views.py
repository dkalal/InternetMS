from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from internetservices.listing import paginate_queryset
from users.permissions import PermissionCode as P
from users.tenancy import require_tenant
from .assignment_forms import AssignmentForm, AssignmentActionForm
from .assignment_services import assignment_queryset, require_actor, save_assignment, manager_action, respond, create_assignment_report
from .forms import WorkReportForm
from .models import WorkAssignment, TechnicianWorkReport


def _manager(request):
    return request.membership.base_role == request.membership.BaseRole.ADMIN_MANAGER


def _query(request):
    tenant = require_tenant(request)
    return assignment_queryset(membership=request.membership, tenant=tenant).select_related("technician__user", "customer", "report")


@login_required
def assignment_list(request):
    qs = _query(request)
    status = request.GET.get("status", "")
    if status in WorkAssignment.Status.values:
        qs = qs.filter(status=status)
    return render(request, "work_reports/assignment_list.html", {
        **paginate_queryset(request, qs, default_page_size=20),
        "manager": _manager(request), "statuses": WorkAssignment.Status.choices,
        "selected_status": status,
    })


@login_required
def assignment_edit(request, pk=None):
    tenant = require_tenant(request)
    require_actor(request.membership, P.WORK_ASSIGNMENTS_MANAGE)
    assignment = get_object_or_404(_query(request), pk=pk) if pk else None
    form = AssignmentForm(request.POST or None, instance=assignment, tenant=tenant, membership=request.membership)
    if request.method == "POST" and form.is_valid():
        try:
            result = save_assignment(
                membership=request.membership, assignment_id=pk,
                version=form.cleaned_data.get("version"), reason=form.cleaned_data["reason"], cleaned_data=form.cleaned_data,
                request_token=form.cleaned_data.get("request_token"),
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            return redirect("work_reports:assignment_detail", pk=result.pk)
    return render(request, "work_reports/assignment_form.html", {"form": form, "assignment": assignment})


@login_required
def assignment_detail(request, pk):
    assignment = get_object_or_404(_query(request), pk=pk)
    manager = _manager(request)
    form = AssignmentActionForm(request.POST or None, manager=manager, status=assignment.status, initial={"version": assignment.version, "revision": assignment.revision})
    if request.method == "POST" and form.is_valid():
        try:
            data = form.cleaned_data
            if manager:
                manager_action(assignment_id=pk, membership=request.membership, version=data["version"], action=data["action"], reason=data["reason"])
            else:
                respond(assignment_id=pk, membership=request.membership, **data)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            return redirect("work_reports:assignment_detail", pk=pk)
    history = assignment.history.select_related("actor_membership__user").filter(tenant=assignment.tenant)
    # Draft/reassignment history may include other Technicians' terms. Own current
    # accepted terms are sufficient; management alone sees the complete history.
    if not manager and request.membership.base_role == request.membership.BaseRole.TECHNICIAN:
        history = history.filter(snapshot__terms__technician_id=request.membership.pk).exclude(to_status="DRAFT")
    linked = getattr(assignment, "report", None)
    return render(request, "work_reports/assignment_detail.html", {
        "assignment": assignment, "manager": manager, "form": form,
        "history_page": paginate_queryset(request, history, default_page_size=20)["page_obj"],
        "linked_report": linked,
        "can_edit": manager and not linked and assignment.status not in {"CANCELLED", "COMPLETED"},
        "can_act": not linked and ((manager and assignment.status not in {"CANCELLED", "COMPLETED"}) or (request.membership.base_role == request.membership.BaseRole.TECHNICIAN and assignment.status == "OFFERED")),
        "can_create_report": request.membership.base_role == request.membership.BaseRole.TECHNICIAN and assignment.status == "ACCEPTED" and not linked,
    })


@login_required
def assignment_report(request, pk):
    from .views import _work_date_formset, _cleaned_work_dates
    assignment = get_object_or_404(_query(request), pk=pk, technician=request.membership)
    require_actor(request.membership, P.WORK_ASSIGNMENTS_REPORT_OWN)
    existing = TechnicianWorkReport.objects.unscoped().filter(tenant=assignment.tenant, assignment=assignment).first()
    if existing:
        return redirect("work_reports:detail", pk=existing.pk)
    if assignment.status != "ACCEPTED":
        raise PermissionDenied("Accept the current offer first.")
    terms = assignment.accepted_terms
    initial = {key: terms[key] for key in ("work_title", "client_name", "work_location")}
    initial.update(customer=assignment.customer, agreed_amount=terms["proposed_amount"])
    form = WorkReportForm(request.POST or None, tenant=assignment.tenant, initial=initial)
    for name in initial:
        form.fields[name].disabled = True
    dates = _work_date_formset(request, tenant=assignment.tenant)
    if request.method == "POST":
        valid = form.is_valid()
        dates_valid = dates.is_valid()
        if valid and dates_valid:
            try:
                report = create_assignment_report(assignment_id=pk, membership=request.membership, cleaned_data=form.cleaned_data, service_days=_cleaned_work_dates(dates))
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            else:
                messages.success(request, "Linked Work Report saved. Review and submit it when ready.")
                return redirect("work_reports:detail", pk=report.pk)
    return render(request, "work_reports/report_form.html", {
        "form": form, "work_date_formset": dates, "page_title": "Report completed assignment",
        "assignment": assignment,
    })
