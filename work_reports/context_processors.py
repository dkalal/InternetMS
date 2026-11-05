from users.permissions import PermissionCode, has_tenant_permission


def work_report_navigation(request):
    tenant = getattr(request, "tenant", None)
    membership = getattr(request, "membership", None)
    pending = 0
    if tenant is not None and membership is not None:
        from django.core.exceptions import PermissionDenied
        from .assignment_services import assignment_queryset
        try:
            assignments = assignment_queryset(membership=membership, tenant=tenant)
            statuses = ["CHANGES_REQUESTED", "DECLINED", "ACCEPTED"] if membership.base_role == membership.BaseRole.ADMIN_MANAGER else ["OFFERED"]
            pending = assignments.filter(status__in=statuses).count()
        except PermissionDenied:
            pass
    if tenant is None or membership is None:
        return {"PENDING_WORK_APPROVALS": 0, "PENDING_ASSIGNMENTS": pending}
    if not has_tenant_permission(
        request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
        membership=membership,
    ):
        return {"PENDING_WORK_APPROVALS": 0, "PENDING_ASSIGNMENTS": pending}
    from .models import TechnicianWorkReport
    return {
        "PENDING_ASSIGNMENTS": pending,
        "PENDING_WORK_APPROVALS": TechnicianWorkReport.objects.filter(
            tenant=tenant, status=TechnicianWorkReport.Status.SUBMITTED,
        ).count(),
    }
