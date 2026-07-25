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
    CUSTOM_FIELD_MANAGE = "custom_field.manage"
    PRODUCT_VIEW = "inventory.product.view"
    PRODUCT_MANAGE = "inventory.product.manage"
    CATEGORY_MANAGE = "inventory.category.manage"
    SUPPLIER_MANAGE = "inventory.supplier.manage"
    PURCHASE_VIEW = "inventory.purchase.view"
    PURCHASE_CONFIRM = "inventory.purchase.confirm"
    STOCK_VIEW = "inventory.stock.view"
    STOCK_ADJUST = "inventory.stock.adjust"
    STOCK_MOVEMENT_VIEW = "inventory.stock_movement.view"
    CART_MANAGE = "inventory.cart.manage"
    COST_REPORT_VIEW = "inventory.cost_report.view"
    SALES_REPORT_VIEW = "inventory.sales_report.view"
    INVENTORY_IMPORT = "inventory.import"
    INVENTORY_EXPORT = "inventory.export"
    INVENTORY_API = "inventory.api"


@dataclass(frozen=True)
class RolePermissionMap:
    role: str
    permissions: set[str]


ROLE_PERMISSIONS: tuple[RolePermissionMap, ...] = (
    RolePermissionMap(
        role=UserAccessProfile.Role.SUPER_ADMIN,
        permissions={
            PermissionCode.TENANT_MANAGE,
            PermissionCode.PLATFORM_ANALYTICS,
            PermissionCode.CUSTOM_FIELD_MANAGE,
            PermissionCode.PRODUCT_VIEW,
            PermissionCode.PRODUCT_MANAGE,
            PermissionCode.CATEGORY_MANAGE,
            PermissionCode.SUPPLIER_MANAGE,
            PermissionCode.PURCHASE_VIEW,
            PermissionCode.PURCHASE_CONFIRM,
            PermissionCode.STOCK_VIEW,
            PermissionCode.STOCK_ADJUST,
            PermissionCode.STOCK_MOVEMENT_VIEW,
            PermissionCode.CART_MANAGE,
            PermissionCode.COST_REPORT_VIEW,
            PermissionCode.SALES_REPORT_VIEW,
            PermissionCode.INVENTORY_IMPORT,
            PermissionCode.INVENTORY_EXPORT,
            PermissionCode.INVENTORY_API,
        },
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
            PermissionCode.CUSTOM_FIELD_MANAGE,
            PermissionCode.PRODUCT_VIEW,
            PermissionCode.PRODUCT_MANAGE,
            PermissionCode.CATEGORY_MANAGE,
            PermissionCode.SUPPLIER_MANAGE,
            PermissionCode.PURCHASE_VIEW,
            PermissionCode.PURCHASE_CONFIRM,
            PermissionCode.STOCK_VIEW,
            PermissionCode.STOCK_ADJUST,
            PermissionCode.STOCK_MOVEMENT_VIEW,
            PermissionCode.CART_MANAGE,
            PermissionCode.COST_REPORT_VIEW,
            PermissionCode.SALES_REPORT_VIEW,
            PermissionCode.INVENTORY_IMPORT,
            PermissionCode.INVENTORY_EXPORT,
            PermissionCode.INVENTORY_API,
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
            PermissionCode.PRODUCT_VIEW,
            PermissionCode.STOCK_VIEW,
            PermissionCode.CART_MANAGE,
            PermissionCode.SALES_REPORT_VIEW,
            PermissionCode.INVENTORY_EXPORT,
            PermissionCode.INVENTORY_API,
        },
    ),
)


def _permissions_for_role(role: str | None) -> set[str]:
    for entry in ROLE_PERMISSIONS:
        if entry.role == role:
            return entry.permissions
    return set()


def user_has_permission(user, permission: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    profile = getattr(user, 'access_profile', None)
    role = getattr(profile, 'role', None)
    if role != UserAccessProfile.Role.SUPER_ADMIN and getattr(profile, 'tenant_id', None):
        from .models import Membership

        membership = Membership.objects.filter(user=user, organization_id=profile.tenant_id, is_active=True).first()
        if membership and membership.role in {Membership.Role.OWNER, Membership.Role.ADMIN}:
            role = UserAccessProfile.Role.TENANT_ADMIN
    return permission in _permissions_for_role(role)


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
