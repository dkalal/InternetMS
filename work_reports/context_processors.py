from users.permissions import PermissionCode, has_tenant_permission


def work_report_navigation(request):
    tenant = getattr(request, "tenant", None)
    membership = getattr(request, "membership", None)
    if tenant is None or membership is None:
        return {"PENDING_WORK_APPROVALS": 0}
    if not has_tenant_permission(
        request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
        membership=membership,
    ):
        return {"PENDING_WORK_APPROVALS": 0}
    from .models import TechnicianWorkReport
    return {
        "PENDING_WORK_APPROVALS": TechnicianWorkReport.objects.filter(
            tenant=tenant, status=TechnicianWorkReport.Status.SUBMITTED,
        ).count(),
    }
