from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from audit.models import AuditLog
from .models import TenantMembership, TenantPermissionGrant


class PermissionCode:
    # Stable public action codes.
    CUSTOMERS_VIEW = "customers.view"
    CUSTOMERS_CREATE = "customers.create"
    CUSTOMERS_UPDATE = "customers.update"
    CUSTOMERS_ARCHIVE = "customers.archive"
    PACKAGES_VIEW = "packages.view"
    PACKAGES_CREATE = "packages.create"
    PACKAGES_UPDATE = "packages.update"
    PRODUCTS_VIEW = "products.view"
    PRODUCTS_CREATE = "products.create"
    PRODUCTS_UPDATE = "products.update"
    INVENTORY_VIEW = "inventory.view"
    INVENTORY_ADJUST = "inventory.adjust"
    PURCHASES_CREATE = "purchases.create"
    REPORTS_VIEW = "reports.view"
    REPORTS_EXPORT = "reports.export"
    USERS_VIEW = "users.view"
    USERS_INVITE = "users.invite"
    USERS_UPDATE_ACCESS = "users.update_access"
    USERS_DEACTIVATE = "users.deactivate"
    TENANT_SETTINGS_VIEW = "tenant_settings.view"
    TENANT_SETTINGS_UPDATE = "tenant_settings.update"
    SALES_DOCUMENTS_VIEW_OWN = "sales_documents.view_own"
    SALES_DOCUMENTS_CREATE = "sales_documents.create"
    QUOTATIONS_UPDATE_OWN_DRAFT = "quotations.update_own_draft"
    QUOTATIONS_SEND = "quotations.send"
    QUOTATIONS_APPROVE = "quotations.approve"
    QUOTATIONS_REJECT = "quotations.reject"
    QUOTATIONS_CANCEL = "quotations.cancel"
    INVOICES_UPDATE_OWN_DRAFT = "invoices.update_own_draft"
    INVOICES_ISSUE_OWN = "invoices.issue_own"
    INVOICES_CANCEL = "invoices.cancel"
    INVOICES_REISSUE = "invoices.reissue"
    PAYMENTS_RECORD_OWN = "payments.record_own"
    RECEIPTS_VIEW = "receipts.view"
    RECEIPTS_ISSUE_OWN = "receipts.issue_own"
    RECEIPTS_VOID = "receipts.void"
    CART_PRICING_OVERRIDE = "cart.pricing_category.override"
    CART_DISCOUNT_APPLY = "cart.discount.apply"
    CART_TAX_RATE_EDIT = "cart.tax_rate.edit"
    FINANCE_DASHBOARD_VIEW = "finance.dashboard.view"
    FINANCE_SALES_VIEW_ALL = "finance.sales.view_all"
    FINANCE_SALES_REPORTS_VIEW = "finance.sales_reports.view"
    FINANCE_REPORTS_EXPORT = "finance.reports.export"
    FINANCE_PURCHASES_VIEW = "finance.purchases.view"
    FINANCE_PURCHASE_COSTS_VIEW = "finance.purchase_costs.view"
    FINANCE_SUPPLIER_BALANCES_VIEW = "finance.supplier_balances.view"
    FINANCE_STOCK_VALUATION_VIEW = "finance.stock_valuation.view"
    FINANCE_PROFITABILITY_VIEW = "finance.profitability.view"
    FINANCE_RECEIVABLES_VIEW = "finance.receivables.view"
    FINANCE_TAX_REPORTS_VIEW = "finance.tax_reports.view"
    FINANCE_RECONCILIATION_VIEW = "finance.reconciliation.view"
    FINANCE_REVERSALS_MANAGE = "finance.reversals.manage"
    FINANCE_AUDIT_VIEW = "finance.audit.view"
    PLATFORM_TENANTS_MANAGE = "platform.tenants.manage"
    PLATFORM_SETTINGS_MANAGE = "platform.settings.manage"
    PLATFORM_AUDIT_VIEW = "platform.audit.view"
    TECHNICIAN_WORK_REPORTS_CREATE_OWN = "technician_work_reports.create_own"
    TECHNICIAN_WORK_REPORTS_VIEW_OWN = "technician_work_reports.view_own"
    TECHNICIAN_WORK_REPORTS_UPDATE_OWN = "technician_work_reports.update_own_draft_or_rejected"
    TECHNICIAN_WORK_REPORTS_SUBMIT_OWN = "technician_work_reports.submit_own"
    TECHNICIAN_WORK_REPORTS_VIEW_ALL = "technician_work_reports.view_all"
    TECHNICIAN_WORK_REPORTS_APPROVE = "technician_work_reports.approve"
    TECHNICIAN_WORK_REPORTS_REJECT = "technician_work_reports.reject"
    TECHNICIAN_WORK_REPORTS_CORRECT_APPROVED = "technician_work_reports.correct_approved"

    # Compatibility aliases used by existing views while they are migrated.
    TENANT_READ = CUSTOMERS_VIEW
    CUSTOMER_CREATE = CUSTOMERS_CREATE
    CUSTOMER_ARCHIVE = CUSTOMERS_ARCHIVE
    BILLING_CREATE = SALES_DOCUMENTS_CREATE
    PAYMENT_REGISTER = PAYMENTS_RECORD_OWN
    WHATSAPP_SEND = CUSTOMERS_VIEW
    USER_MANAGE = USERS_UPDATE_ACCESS
    BILLING_SETTINGS_CHANGE = TENANT_SETTINGS_UPDATE
    TENANT_MANAGE = PLATFORM_TENANTS_MANAGE
    PLATFORM_ANALYTICS = PLATFORM_AUDIT_VIEW
    CUSTOM_FIELD_MANAGE = TENANT_SETTINGS_UPDATE
    PRODUCT_VIEW = PRODUCTS_VIEW
    PRODUCT_MANAGE = PRODUCTS_UPDATE
    CATEGORY_MANAGE = PRODUCTS_UPDATE
    SUPPLIER_MANAGE = FINANCE_PURCHASES_VIEW
    PURCHASE_VIEW = FINANCE_PURCHASES_VIEW
    PURCHASE_CONFIRM = FINANCE_PURCHASES_VIEW
    STOCK_VIEW = INVENTORY_VIEW
    STOCK_ADJUST = INVENTORY_ADJUST
    STOCK_MOVEMENT_VIEW = INVENTORY_VIEW
    CART_MANAGE = SALES_DOCUMENTS_CREATE
    COST_REPORT_VIEW = FINANCE_STOCK_VALUATION_VIEW
    SALES_REPORT_VIEW = FINANCE_SALES_REPORTS_VIEW
    INVENTORY_IMPORT = INVENTORY_ADJUST
    INVENTORY_EXPORT = REPORTS_EXPORT
    INVENTORY_API = INVENTORY_VIEW


MANAGER_FINANCE_PERMISSIONS = frozenset({
    PermissionCode.FINANCE_DASHBOARD_VIEW, PermissionCode.FINANCE_SALES_VIEW_ALL,
    PermissionCode.FINANCE_SALES_REPORTS_VIEW, PermissionCode.FINANCE_REPORTS_EXPORT,
    PermissionCode.FINANCE_PURCHASES_VIEW, PermissionCode.FINANCE_PURCHASE_COSTS_VIEW,
    PermissionCode.FINANCE_SUPPLIER_BALANCES_VIEW, PermissionCode.FINANCE_STOCK_VALUATION_VIEW,
    PermissionCode.FINANCE_PROFITABILITY_VIEW, PermissionCode.FINANCE_RECEIVABLES_VIEW,
    PermissionCode.FINANCE_TAX_REPORTS_VIEW, PermissionCode.FINANCE_RECONCILIATION_VIEW,
    PermissionCode.FINANCE_REVERSALS_MANAGE, PermissionCode.FINANCE_AUDIT_VIEW,
    PermissionCode.INVOICES_CANCEL, PermissionCode.INVOICES_REISSUE, PermissionCode.RECEIPTS_VOID,
})

PLATFORM_PERMISSIONS = frozenset({
    PermissionCode.PLATFORM_TENANTS_MANAGE,
    PermissionCode.PLATFORM_SETTINGS_MANAGE,
    PermissionCode.PLATFORM_AUDIT_VIEW,
})

SALES_BASELINE = frozenset({
    PermissionCode.CUSTOMERS_VIEW, PermissionCode.CUSTOMERS_CREATE, PermissionCode.CUSTOMERS_UPDATE,
    PermissionCode.PACKAGES_VIEW, PermissionCode.PRODUCTS_VIEW, PermissionCode.INVENTORY_VIEW,
    PermissionCode.SALES_DOCUMENTS_VIEW_OWN, PermissionCode.SALES_DOCUMENTS_CREATE,
    PermissionCode.QUOTATIONS_UPDATE_OWN_DRAFT, PermissionCode.QUOTATIONS_SEND,
    PermissionCode.INVOICES_UPDATE_OWN_DRAFT, PermissionCode.INVOICES_ISSUE_OWN,
    PermissionCode.PAYMENTS_RECORD_OWN, PermissionCode.RECEIPTS_VIEW,
    PermissionCode.RECEIPTS_ISSUE_OWN,
})

TECHNICIAN_BASELINE = frozenset({
    PermissionCode.TECHNICIAN_WORK_REPORTS_CREATE_OWN,
    PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_OWN,
    PermissionCode.TECHNICIAN_WORK_REPORTS_UPDATE_OWN,
    PermissionCode.TECHNICIAN_WORK_REPORTS_SUBMIT_OWN,
})

WORK_REPORT_MANAGEMENT_PERMISSIONS = frozenset({
    PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
    PermissionCode.TECHNICIAN_WORK_REPORTS_APPROVE,
    PermissionCode.TECHNICIAN_WORK_REPORTS_REJECT,
    PermissionCode.TECHNICIAN_WORK_REPORTS_CORRECT_APPROVED,
})

TENANT_OPERATION_PERMISSIONS = frozenset({
    PermissionCode.CUSTOMERS_VIEW, PermissionCode.CUSTOMERS_CREATE, PermissionCode.CUSTOMERS_UPDATE,
    PermissionCode.CUSTOMERS_ARCHIVE,
    PermissionCode.PACKAGES_VIEW, PermissionCode.PACKAGES_CREATE, PermissionCode.PACKAGES_UPDATE,
    PermissionCode.PRODUCTS_VIEW, PermissionCode.PRODUCTS_CREATE, PermissionCode.PRODUCTS_UPDATE,
    PermissionCode.INVENTORY_VIEW, PermissionCode.INVENTORY_ADJUST, PermissionCode.PURCHASES_CREATE,
    PermissionCode.REPORTS_VIEW, PermissionCode.REPORTS_EXPORT,
    PermissionCode.USERS_VIEW, PermissionCode.USERS_INVITE, PermissionCode.USERS_UPDATE_ACCESS,
    PermissionCode.USERS_DEACTIVATE, PermissionCode.TENANT_SETTINGS_VIEW,
    PermissionCode.TENANT_SETTINGS_UPDATE, PermissionCode.SALES_DOCUMENTS_VIEW_OWN,
    PermissionCode.SALES_DOCUMENTS_CREATE, PermissionCode.QUOTATIONS_UPDATE_OWN_DRAFT,
    PermissionCode.QUOTATIONS_SEND, PermissionCode.QUOTATIONS_APPROVE,
    PermissionCode.QUOTATIONS_REJECT, PermissionCode.QUOTATIONS_CANCEL,
    PermissionCode.INVOICES_UPDATE_OWN_DRAFT, PermissionCode.INVOICES_ISSUE_OWN,
    PermissionCode.PAYMENTS_RECORD_OWN, PermissionCode.RECEIPTS_VIEW,
    PermissionCode.RECEIPTS_ISSUE_OWN,
    PermissionCode.CART_PRICING_OVERRIDE, PermissionCode.CART_DISCOUNT_APPLY,
    PermissionCode.CART_TAX_RATE_EDIT,
}) | MANAGER_FINANCE_PERMISSIONS | WORK_REPORT_MANAGEMENT_PERMISSIONS

DELEGABLE_PERMISSIONS = frozenset({
    PermissionCode.CUSTOMERS_VIEW, PermissionCode.CUSTOMERS_CREATE, PermissionCode.CUSTOMERS_UPDATE,
    PermissionCode.PACKAGES_VIEW, PermissionCode.PRODUCTS_VIEW, PermissionCode.INVENTORY_VIEW,
    PermissionCode.SALES_DOCUMENTS_VIEW_OWN, PermissionCode.SALES_DOCUMENTS_CREATE,
    PermissionCode.QUOTATIONS_UPDATE_OWN_DRAFT, PermissionCode.QUOTATIONS_SEND,
    PermissionCode.INVOICES_UPDATE_OWN_DRAFT, PermissionCode.INVOICES_ISSUE_OWN,
    PermissionCode.PAYMENTS_RECORD_OWN, PermissionCode.RECEIPTS_VIEW,
    PermissionCode.RECEIPTS_ISSUE_OWN,
    PermissionCode.CART_PRICING_OVERRIDE, PermissionCode.CART_DISCOUNT_APPLY,
    PermissionCode.CART_TAX_RATE_EDIT,
})


def membership_for(user, tenant):
    if not user or not user.is_authenticated or tenant is None:
        return None
    return TenantMembership.objects.filter(user=user, tenant=tenant, is_active=True).first()


def permissions_for_membership(membership) -> set[str]:
    if membership is None or not membership.is_active:
        return set()
    role = membership.base_role
    if role == TenantMembership.BaseRole.SUPER_ADMIN:
        return set(TENANT_OPERATION_PERMISSIONS | PLATFORM_PERMISSIONS)
    if role == TenantMembership.BaseRole.ADMIN_MANAGER:
        return set(TENANT_OPERATION_PERMISSIONS)
    if role == TenantMembership.BaseRole.SALES:
        baseline = set(SALES_BASELINE)
    elif role == TenantMembership.BaseRole.TECHNICIAN:
        baseline = set(TECHNICIAN_BASELINE)
    else:
        baseline = set()
    grants = set(membership.permission_grants.values_list("action_code", flat=True))
    # Database tampering must not bypass the non-delegable boundary.
    grants &= set(DELEGABLE_PERMISSIONS)
    return baseline | grants


def has_tenant_permission(user, tenant, action_code: str, *, membership=None) -> bool:
    membership = membership or membership_for(user, tenant)
    if membership is None:
        return False
    if membership.base_role != TenantMembership.BaseRole.SUPER_ADMIN and membership.tenant_id != getattr(tenant, "id", None):
        return False
    return action_code in permissions_for_membership(membership)


def permission_grant_for(membership, action_code: str):
    """Return the configured exception grant; managers are policy-unlimited."""
    if membership is None or membership.base_role in {
        TenantMembership.BaseRole.SUPER_ADMIN,
        TenantMembership.BaseRole.ADMIN_MANAGER,
    }:
        return None
    return membership.permission_grants.filter(action_code=action_code).first()


def user_has_permission(user, permission: str) -> bool:
    """Compatibility helper; request-aware code should call has_tenant_permission."""
    memberships = TenantMembership.objects.filter(user=user, is_active=True).select_related("tenant")
    return any(permission in permissions_for_membership(item) for item in memberships)


def require_tenant_context(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise PermissionDenied("Tenant context is required.")
    return tenant


def ensure_object_tenant_access(request, obj):
    tenant = require_tenant_context(request)
    if getattr(obj, "tenant_id", tenant.id) != tenant.id:
        raise PermissionDenied("Object is not available in this tenant.")


def require_permission(request, permission: str, *, obj=None):
    tenant = require_tenant_context(request)
    membership = getattr(request, "membership", None)
    if not has_tenant_permission(request.user, tenant, permission, membership=membership):
        raise PermissionDenied("Insufficient permissions.")
    if obj is not None:
        ensure_object_tenant_access(request, obj)
    return True


def sales_document_queryset_for(user, tenant, queryset=None, *, membership=None):
    from billing.models import BillingDocument

    queryset = queryset if queryset is not None else BillingDocument.objects.unscoped().all()
    queryset = queryset.filter(tenant=tenant)
    membership = membership or membership_for(user, tenant)
    if membership is None:
        return queryset.none()
    if membership.base_role in {TenantMembership.BaseRole.SUPER_ADMIN, TenantMembership.BaseRole.ADMIN_MANAGER}:
        return queryset
    if membership.base_role != TenantMembership.BaseRole.SALES:
        return queryset.none()
    return queryset.filter(Q(created_by_membership=membership) | Q(responsible_membership=membership)).distinct()


def can_view_sales_document(user, document, *, membership=None) -> bool:
    return sales_document_queryset_for(
        user, document.tenant, membership=membership
    ).filter(pk=document.pk).exists()


def can_record_payment(user, invoice, *, membership=None) -> bool:
    from billing.models import BillingDocument

    if invoice.document_type != BillingDocument.DocumentType.INVOICE:
        return False
    if invoice.status not in {
        BillingDocument.Status.ISSUED,
        BillingDocument.Status.SENT,
        BillingDocument.Status.PARTIALLY_PAID,
    }:
        return False
    membership = membership or membership_for(user, invoice.tenant)
    if not has_tenant_permission(user, invoice.tenant, PermissionCode.PAYMENTS_RECORD_OWN, membership=membership):
        return False
    return can_view_sales_document(user, invoice, membership=membership)


def financial_report_queryset_for(user, tenant, queryset, *, membership=None):
    membership = membership or membership_for(user, tenant)
    if not has_tenant_permission(user, tenant, PermissionCode.FINANCE_SALES_REPORTS_VIEW, membership=membership):
        return queryset.none()
    return queryset.filter(tenant=tenant)


def validate_delegated_grant(*, actor_membership, target_membership, action_code, scope):
    if actor_membership.pk == target_membership.pk:
        raise PermissionDenied("Users cannot change their own access.")
    if actor_membership.tenant_id != target_membership.tenant_id:
        raise PermissionDenied("Cross-tenant permission changes are forbidden.")
    if actor_membership.base_role != TenantMembership.BaseRole.ADMIN_MANAGER:
        raise PermissionDenied("Only an Administrator / Manager can delegate tenant access.")
    if target_membership.base_role == TenantMembership.BaseRole.SUPER_ADMIN:
        raise PermissionDenied("Super Administrator access is platform-managed.")
    if action_code not in DELEGABLE_PERMISSIONS or action_code in MANAGER_FINANCE_PERMISSIONS | PLATFORM_PERMISSIONS:
        raise PermissionDenied("This permission is not delegable.")
    if target_membership.base_role in {TenantMembership.BaseRole.SALES, TenantMembership.BaseRole.TECHNICIAN} and scope == TenantPermissionGrant.Scope.TENANT_ALL:
        raise PermissionDenied("Tenant-wide financial scope cannot be delegated.")
