from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from internetservices.listing import paginate_queryset
from users.models import TenantMembership
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


def _format_history_value(name, value):
    if value in (None, ""):
        return "Not set"
    if name == "proposed_amount":
        try:
            return "TZS {:,.2f}".format(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError):
            return str(value)
    if name in {"proposed_start", "proposed_end"}:
        try:
            parsed = date.fromisoformat(value)
            return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _history_timeline(page, *, manager, membership, tenant):
    entries = list(page)
    snapshots = [entry.snapshot or {} for entry in entries]
    membership_ids = {
        terms.get("technician_id")
        for snapshot in snapshots
        for terms in (snapshot.get("terms", {}), snapshot.get("previous_terms", {}))
        if terms.get("technician_id")
    }
    technicians = {
        item.pk: str(item.user)
        for item in TenantMembership.objects.filter(tenant=tenant, pk__in=membership_ids).select_related("user")
    }
    labels = dict(WorkAssignment.Status.choices)
    field_labels = {
        "work_title": "Work title", "scope": "Scope", "client_name": "Client",
        "work_location": "Location", "proposed_amount": "Payment amount",
        "proposed_start": "Start date", "proposed_end": "End date",
        "instructions": "Instructions",
    }
    event_styles = {
        "CREATED": "slate", "REVISED": "indigo", "SEND": "blue",
        "ACCEPT": "emerald", "DECLINE": "rose", "REQUEST_CHANGES": "amber",
        "CANCEL": "rose", "REPORT_CREATED": "violet", "COMPLETED": "emerald",
    }
    timeline = []
    for entry, snapshot in zip(entries, snapshots):
        terms = snapshot.get("terms", {})
        previous = snapshot.get("previous_terms", {})
        suggestions = snapshot.get("suggestions", {})
        previous_belongs_to_actor = previous.get("technician_id") in (None, membership.pk)
        changed_terms = []
        if previous and (manager or previous_belongs_to_actor):
            for name, label in field_labels.items():
                old, new = previous.get(name), terms.get(name)
                if old != new:
                    changed_terms.append({
                        "label": label,
                        "old": _format_history_value(name, old),
                        "new": _format_history_value(name, new),
                        "long_text": name in {"scope", "instructions"},
                    })
            if manager and previous.get("technician_id") != terms.get("technician_id"):
                changed_terms.insert(0, {
                    "label": "Technician",
                    "old": technicians.get(previous.get("technician_id"), "Previous Technician"),
                    "new": technicians.get(terms.get("technician_id"), "Current Technician"),
                })
        if entry.event == "CREATED":
            summary = "Draft assignment created"
        elif entry.event == "REVISED":
            summary = "Assignment terms updated"
        elif entry.event == "SEND":
            summary = f"Offer sent to {technicians.get(terms.get('technician_id'), 'the Technician')}"
        elif entry.event == "REQUEST_CHANGES":
            summary = "Technician requested changes"
        elif entry.event == "REPORT_CREATED":
            summary = "Work Report created"
        elif entry.event == "COMPLETED":
            summary = "Assignment completed after report approval"
        else:
            summary = entry.event_label
        shown_suggestions = []
        if entry.event == "REQUEST_CHANGES":
            for name, label in (("proposed_amount", "Payment amount"), ("proposed_start", "Start date"), ("proposed_end", "End date")):
                if suggestions.get(name) not in (None, ""):
                    shown_suggestions.append({"label": label, "value": _format_history_value(name, suggestions[name])})
        timeline.append({
            "entry": entry, "summary": summary, "style": event_styles.get(entry.event, "slate"),
            "from_status_label": labels.get(entry.from_status, entry.from_status or "—"),
            "to_status_label": labels.get(entry.to_status, entry.to_status),
            "changed_terms": changed_terms, "suggestions": shown_suggestions,
            "terms": terms,
        })
    return timeline


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
    requested_changes = {}
    if assignment:
        change_event = assignment.history.filter(
            tenant=tenant, event="REQUEST_CHANGES",
        ).order_by("-created_at", "-pk").first()
        suggestions = (change_event.snapshot or {}).get("suggestions", {}) if change_event else {}
        if suggestions.get("proposed_amount") not in (None, ""):
            try:
                requested_changes["proposed_amount"] = "TZS {:,.2f}".format(Decimal(str(suggestions["proposed_amount"])))
            except (InvalidOperation, TypeError, ValueError):
                pass
        for field_name in ("proposed_start", "proposed_end"):
            raw_date = suggestions.get(field_name)
            if raw_date:
                try:
                    parsed_date = date.fromisoformat(raw_date)
                    requested_changes[field_name] = f"{parsed_date.strftime('%b')} {parsed_date.day}, {parsed_date.year}"
                except (TypeError, ValueError):
                    requested_changes[field_name] = str(raw_date)
    form_fields = [
        {"field": field, "requested_change": requested_changes.get(field.name)}
        for field in form.visible_fields()
    ]
    assignment_action = request.POST.get("assignment_action", "") if request.method == "POST" else ""
    if request.method == "POST" and assignment_action == "cancel" and assignment:
        action_form = AssignmentActionForm(request.POST, manager=True, status=assignment.status)
        if action_form.is_valid():
            try:
                manager_action(
                    assignment_id=pk, membership=request.membership,
                    version=action_form.cleaned_data["version"], action="cancel",
                    reason=action_form.cleaned_data["reason"],
                )
            except ValidationError as exc:
                action_form.add_error(None, "; ".join(exc.messages))
            else:
                messages.success(request, "Assignment cancelled.")
                return redirect("work_reports:assignment_detail", pk=pk)
        return render(request, "work_reports/assignment_form.html", {
            "form": form, "assignment": assignment, "action_form": action_form,
            "can_send": False, "can_cancel": True, "form_fields": form_fields,
        })
    if request.method == "POST" and form.is_valid():
        try:
            result = save_assignment(
                membership=request.membership, assignment_id=pk,
                version=form.cleaned_data.get("version"), reason=form.cleaned_data["reason"], cleaned_data=form.cleaned_data,
                request_token=form.cleaned_data.get("request_token"),
            )
            if assignment_action == "send":
                result = manager_action(
                    assignment_id=result.pk, membership=request.membership,
                    version=result.version, action="send",
                )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            return redirect("work_reports:assignment_detail", pk=result.pk)
    return render(request, "work_reports/assignment_form.html", {
        "form": form, "assignment": assignment, "form_fields": form_fields,
        "can_send": not assignment or (not getattr(assignment, "report", None) and assignment.status in {
            WorkAssignment.Status.DRAFT, WorkAssignment.Status.OFFERED,
            WorkAssignment.Status.CHANGES_REQUESTED, WorkAssignment.Status.DECLINED,
            WorkAssignment.Status.ACCEPTED,
        }),
        "can_cancel": assignment and not getattr(assignment, "report", None) and assignment.status not in {WorkAssignment.Status.CANCELLED, WorkAssignment.Status.COMPLETED},
    })


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
    history_page = paginate_queryset(request, history, default_page_size=20)["page_obj"]
    linked = getattr(assignment, "report", None)
    return render(request, "work_reports/assignment_detail.html", {
        "assignment": assignment, "manager": manager, "form": form,
        "history_page": history_page,
        "history_items": _history_timeline(
            history_page, manager=manager, membership=request.membership, tenant=assignment.tenant,
        ),
        "linked_report": linked,
        "can_edit": manager and not linked and assignment.status not in {"CANCELLED", "COMPLETED"},
        "can_act": not linked and not manager and request.membership.base_role == request.membership.BaseRole.TECHNICIAN and assignment.status == "OFFERED",
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
