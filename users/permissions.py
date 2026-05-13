from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied

from audit.models import AuditLog
from .models import UserAccessProfile


class PermissionCode:
    TENANT_READ = "tenant.read"
    CUSTOMER_CREATE = "customer.create"
    CUSTOMER_ARCHIVE = "customer.archive"
    BILLING_CREATE = "billing.create"
    PAYMENT_REGISTER = "payment.register"
    WHATSAPP_SEND = "whatsapp.send"
    USER_MANAGE = "user.manage"
    BILLING_SETTINGS_CHANGE = "billing.settings.change"
    TENANT_MANAGE = "tenant.manage"
    PLATFORM_ANALYTICS = "platform.analytics"


@dataclass(frozen=True)
class RolePermissionMap:
    role: str
    permissions: set[str]


ROLE_PERMISSIONS: tuple[RolePermissionMap, ...] = (
    RolePermissionMap(
        role=UserAccessProfile.Role.SUPER_ADMIN,
        permissions={PermissionCode.TENANT_MANAGE, PermissionCode.PLATFORM_ANALYTICS},
    ),
    RolePermissionMap(
        role=UserAccessProfile.Role.TENANT_ADMIN,
        permissions={
            PermissionCode.TENANT_READ,
            PermissionCode.CUSTOMER_CREATE,
            PermissionCode.CUSTOMER_ARCHIVE,
            PermissionCode.BILLING_CREATE,
            PermissionCode.PAYMENT_REGISTER,
            PermissionCode.WHATSAPP_SEND,
            PermissionCode.USER_MANAGE,
            PermissionCode.BILLING_SETTINGS_CHANGE,
        },
    ),
    RolePermissionMap(
        role=UserAccessProfile.Role.TENANT_STAFF,
        permissions={
            PermissionCode.TENANT_READ,
            PermissionCode.CUSTOMER_CREATE,
            PermissionCode.BILLING_CREATE,
            PermissionCode.PAYMENT_REGISTER,
            PermissionCode.WHATSAPP_SEND,
        },
    ),
)


def _permissions_for_role(role: str | None) -> set[str]:
    for entry in ROLE_PERMISSIONS:
        if entry.role == role:
            return entry.permissions
    return set()


def require_tenant_context(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise PermissionDenied("Tenant context is required.")
    return tenant


def ensure_object_tenant_access(request, obj):
    obj_tenant_id = getattr(obj, "tenant_id", None)
    req_tenant_id = getattr(getattr(request, "tenant", None), "id", None)
    if obj_tenant_id is not None and req_tenant_id is not None and obj_tenant_id != req_tenant_id:
        AuditLog.objects.create(
            organization_id=obj_tenant_id,
            actor=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
            action="security.cross_tenant_access_attempt",
            object_type=obj.__class__.__name__,
            object_id=str(getattr(obj, "id", "")),
            metadata={"request_tenant_id": req_tenant_id},
        )
        raise PermissionDenied("Cross-tenant object access denied.")


def require_permission(request, permission: str, *, obj=None):
    role = getattr(request, "user_role", None)
    allowed = _permissions_for_role(role)
    if permission not in allowed:
        raise PermissionDenied("Insufficient permissions.")
    if role != UserAccessProfile.Role.SUPER_ADMIN:
        require_tenant_context(request)
    if obj is not None:
        ensure_object_tenant_access(request, obj)
    return True
