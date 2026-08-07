from users.models import TenantMembership
from users.permissions import PermissionCode, has_tenant_permission

from .models import TechnicianWorkReport


def work_report_queryset_for(user, tenant, queryset=None, *, membership=None):
    queryset = queryset if queryset is not None else TechnicianWorkReport.objects.unscoped().all()
    queryset = queryset.filter(tenant=tenant)
    membership = membership or TenantMembership.objects.filter(
        user=user, tenant=tenant, is_active=True,
    ).first()
    if membership is None:
        return queryset.none()
    if has_tenant_permission(
        user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
        membership=membership,
    ):
        return queryset
    if has_tenant_permission(
        user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_OWN,
        membership=membership,
    ):
        return queryset.filter(technician=membership)
    return queryset.none()


def pending_approval_queryset_for(user, tenant, *, membership=None):
    return work_report_queryset_for(user, tenant, membership=membership).filter(
        status=TechnicianWorkReport.Status.SUBMITTED,
    )
