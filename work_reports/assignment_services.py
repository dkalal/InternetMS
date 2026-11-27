"""Trusted assignment boundary: authorization, locks, transitions and history."""
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from users.models import TenantMembership
from users.permissions import PermissionCode as P, has_tenant_permission
from .models import WorkAssignment as A, WorkAssignmentHistory, TechnicianWorkReport

EDIT_FIELDS = (
    "technician", "customer", "work_title", "scope", "client_name", "work_location",
    "proposed_start", "proposed_end", "proposed_amount", "instructions", "management_notes",
)
EDITABLE = {A.Status.DRAFT, A.Status.OFFERED, A.Status.CHANGES_REQUESTED, A.Status.DECLINED, A.Status.ACCEPTED}
TRANSITIONS = {
    "send": ({A.Status.DRAFT}, A.Status.OFFERED),
    "accept": ({A.Status.OFFERED}, A.Status.ACCEPTED),
    "decline": ({A.Status.OFFERED}, A.Status.DECLINED),
    "request_changes": ({A.Status.OFFERED}, A.Status.CHANGES_REQUESTED),
    "revise": (EDITABLE, A.Status.DRAFT),
    "cancel": (EDITABLE, A.Status.CANCELLED),
    "complete": ({A.Status.ACCEPTED}, A.Status.COMPLETED),
}


def _transition(assignment, action):
    allowed, target = TRANSITIONS[action]
    if assignment.status not in allowed:
        raise ValidationError("This action is not available in the assignment's current state.")
    assignment.status = target


def require_actor(membership, permission, *, tenant=None):
    tenant = tenant or (membership.tenant if membership else None)
    current = TenantMembership.objects.select_related("user", "tenant").filter(
        pk=getattr(membership, "pk", None), is_active=True, user__is_active=True,
    ).first()
    if not current or not current.tenant.is_active or not tenant or not type(tenant).objects.filter(pk=tenant.pk, is_active=True).exists() or not has_tenant_permission(
        current.user, tenant, permission, membership=current,
    ):
        raise PermissionDenied("Assignment is not available.")
    if permission == P.WORK_ASSIGNMENTS_MANAGE and (
        current.base_role != TenantMembership.BaseRole.ADMIN_MANAGER or current.tenant_id != tenant.pk
    ):
        raise PermissionDenied("Only tenant Managers manage assignments.")
    return current


def assignment_queryset(*, membership, tenant):
    try:
        actor = require_actor(membership, P.WORK_ASSIGNMENTS_VIEW_ALL, tenant=tenant)
    except PermissionDenied:
        actor = require_actor(membership, P.WORK_ASSIGNMENTS_VIEW_OWN, tenant=tenant)
        return A.objects.unscoped().filter(tenant=tenant, technician=actor).exclude(status=A.Status.DRAFT)
    return A.objects.unscoped().filter(tenant=tenant)


def _history(assignment, actor, event, old="", reason="", extra=None):
    WorkAssignmentHistory.objects.create(
        tenant=assignment.tenant, assignment=assignment, actor_membership=actor,
        event=event, from_status=old, to_status=assignment.status,
        revision=assignment.revision, reason=reason,
        snapshot={"terms": assignment.terms(), **(extra or {})},
    )
    AuditLog.objects.create(
        organization=assignment.tenant, tenant=assignment.tenant, actor=actor.user,
        action=f"work_assignment.{event.lower()}", object_type="WorkAssignment",
        object_id=str(assignment.pk), old_value={"status": old},
        new_value={"status": assignment.status, "revision": assignment.revision},
    )


def _save(assignment):
    assignment._service_write = True
    try:
        assignment.save()
    finally:
        del assignment._service_write


def _lock(pk, actor, version):
    assignment = A.objects.unscoped().select_for_update().filter(pk=pk, tenant=actor.tenant).first()
    if not assignment:
        raise PermissionDenied("Assignment is not available.")
    if assignment.version != version:
        raise ValidationError("This offer has changed. Reload and review the current offer.")
    return assignment


def _unlinked(assignment):
    if TechnicianWorkReport.objects.unscoped().filter(assignment=assignment).exists():
        raise ValidationError("A Work Report already exists. Use its correction and review workflow.")


def _validate_assignee(assignment):
    if not TenantMembership.objects.filter(
        pk=assignment.technician_id, tenant=assignment.tenant, is_active=True,
        user__is_active=True, base_role=TenantMembership.BaseRole.TECHNICIAN,
    ).exists():
        raise ValidationError("Choose an active Technician in this tenant.")


@transaction.atomic
def save_assignment(*, membership, cleaned_data, assignment_id=None, version=None, reason="", request_token=None):
    actor = require_actor(membership, P.WORK_ASSIGNMENTS_MANAGE)
    request_key = None
    if assignment_id is None and request_token:
        try:
            payload = signing.loads(request_token, salt="work-assignment-create", max_age=86400)
            request_key = UUID(payload["key"])
            if payload["tenant"] != actor.tenant_id or payload["creator"] != actor.pk:
                raise PermissionDenied("This assignment form belongs to another account or tenant.")
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            raise ValidationError("This form has expired or changed. Open a new assignment form.")
        # Serialize creation retries by this creator. The unique key reinforces
        # the guarantee without requiring a pre-existing assignment to lock.
        TenantMembership.objects.select_for_update().get(pk=actor.pk)
        existing = A.objects.unscoped().filter(
            tenant=actor.tenant, creator=actor, request_key=request_key,
        ).first()
        if existing:
            return existing
    assignment = _lock(assignment_id, actor, version) if assignment_id else A(tenant=actor.tenant, creator=actor)
    if request_key:
        assignment.request_key = request_key
    old = assignment.status
    previous = assignment.terms() if assignment.pk else {}
    if assignment.pk:
        _unlinked(assignment)
        if old not in EDITABLE:
            raise ValidationError("This assignment cannot be revised.")
    for field in EDIT_FIELDS:
        if field in cleaned_data:
            setattr(assignment, field, cleaned_data[field])
    if previous and previous["technician_id"] != assignment.technician_id and not reason.strip():
        raise ValidationError("A reassignment reason is required.")
    _validate_assignee(assignment)
    _transition(assignment, "revise")
    if assignment.pk:
        assignment.version += 1
    _save(assignment)
    _history(assignment, actor, "REVISED" if previous else "CREATED", old, reason.strip(), {"previous_terms": previous})
    return assignment


@transaction.atomic
def manager_action(*, assignment_id, membership, version, action, reason=""):
    actor = require_actor(membership, P.WORK_ASSIGNMENTS_MANAGE)
    assignment = _lock(assignment_id, actor, version)
    _unlinked(assignment)
    old = assignment.status
    if action == "send" and old == A.Status.DRAFT:
        _validate_assignee(assignment)
        assignment.revision += 1
        _transition(assignment, "send")
        assignment.sent_at = timezone.now()
    elif action == "cancel" and old in EDITABLE and reason.strip():
        _transition(assignment, "cancel")
        assignment.cancelled_at = timezone.now()
    else:
        raise ValidationError("Invalid action or state. Cancellation requires a reason.")
    assignment.version += 1
    _save(assignment)
    _history(assignment, actor, action.upper(), old, reason.strip())
    return assignment


@transaction.atomic
def respond(*, assignment_id, membership, version, revision, action, reason="", proposed_amount=None, proposed_start=None, proposed_end=None):
    actor = require_actor(membership, P.WORK_ASSIGNMENTS_RESPOND_OWN)
    assignment = _lock(assignment_id, actor, version)
    if assignment.technician_id != actor.pk:
        raise PermissionDenied("Assignment is not available.")
    if assignment.status != A.Status.OFFERED or assignment.revision != revision:
        raise ValidationError("Review the current offered revision before responding.")
    if action not in {"accept", "decline", "request_changes"} or (action != "accept" and not reason.strip()):
        raise ValidationError("Choose a valid response and provide the required reason.")
    extra = {}
    if proposed_amount is not None:
        try:
            amount = Decimal(str(proposed_amount))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Enter a valid proposed amount.")
        if not amount.is_finite() or amount < 0 or amount >= Decimal("1000000000000") or amount.as_tuple().exponent < -2:
            raise ValidationError("Enter a valid proposed amount with at most two decimals.")
        extra["proposed_amount"] = str(amount)
    if proposed_start and proposed_end and proposed_end < proposed_start:
        raise ValidationError("Proposed end must follow proposed start.")
    for key, value in (("proposed_start", proposed_start), ("proposed_end", proposed_end)):
        if value:
            extra[key] = value.isoformat()
    old = assignment.status
    _transition(assignment, action)
    if action == "accept":
        assignment.accepted_revision = revision
        assignment.accepted_terms = assignment.terms()
        assignment.accepted_at = timezone.now()
    elif action == "decline":
        assignment.declined_at = timezone.now()
    assignment.version += 1
    _save(assignment)
    _history(assignment, actor, action.upper(), old, reason.strip(), {"suggestions": extra})
    return assignment


@transaction.atomic
def create_assignment_report(*, assignment_id, membership, cleaned_data, service_days):
    from .services import create_report
    actor = require_actor(membership, P.WORK_ASSIGNMENTS_REPORT_OWN)
    assignment = A.objects.unscoped().select_for_update().filter(
        pk=assignment_id, tenant=actor.tenant, technician=actor,
    ).first()
    if not assignment:
        raise PermissionDenied("Assignment is not available.")
    existing = TechnicianWorkReport.objects.unscoped().filter(assignment=assignment).first()
    if existing:
        return existing
    if assignment.status != A.Status.ACCEPTED or assignment.accepted_revision != assignment.revision:
        raise ValidationError("Accept the current offer before creating a Work Report.")
    terms = assignment.accepted_terms
    data = {key: cleaned_data[key] for key in ("activity_description", "internal_notes") if key in cleaned_data}
    data.update({key: terms[key] for key in ("work_title", "client_name", "work_location")})
    data.update(customer=assignment.customer, agreed_amount=Decimal(terms["proposed_amount"]), assignment=assignment)
    report = create_report(membership=actor, cleaned_data=data, service_days=service_days)
    _history(assignment, actor, "REPORT_CREATED", assignment.status, extra={"report_id": report.pk})
    return report


@transaction.atomic
def complete_assignment(report, actor):
    if not report.assignment_id:
        return
    actor = require_actor(actor, P.TECHNICIAN_WORK_REPORTS_APPROVE, tenant=report.tenant)
    report = TechnicianWorkReport.objects.unscoped().select_for_update().get(
        pk=report.pk, tenant=report.tenant,
    )
    if report.status != TechnicianWorkReport.Status.APPROVED:
        raise ValidationError("Only an approved Work Report completes an assignment.")
    assignment = A.objects.unscoped().select_for_update().get(pk=report.assignment_id, tenant=report.tenant)
    if assignment.status == A.Status.COMPLETED:
        return
    if assignment.status != A.Status.ACCEPTED:
        raise ValidationError("Linked assignment must remain accepted until report approval.")
    old = assignment.status
    _transition(assignment, "complete")
    assignment.completed_at = timezone.now()
    assignment.version += 1
    _save(assignment)
    _history(assignment, actor, "COMPLETED", old, extra={"report_id": report.pk})
