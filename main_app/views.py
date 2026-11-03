from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from billing.models import BillingDocument, CustomerSubscription
from customers.models import Customer, InternetService
from users.models import TenantMembership
from users.permissions import PermissionCode, has_tenant_permission, sales_document_queryset_for
from users.tenancy import require_tenant
from work_reports.models import TechnicianWorkReport


@login_required
def workspace_home(request):
    """The safe, role-aware destination for a normal authenticated entry."""
    membership = request.membership

    # Platform operators choose a tenant deliberately; do not silently place a
    # Super Administrator in any customer's workspace.
    if membership.base_role == TenantMembership.BaseRole.SUPER_ADMIN and not request.support_access:
        return redirect("start_support_access")

    tenant = require_tenant(request)

    # Technician work is intentionally isolated from the commercial workspace.
    if membership.base_role == TenantMembership.BaseRole.TECHNICIAN:
        return redirect("work_reports:list")

    documents = sales_document_queryset_for(
        request.user, tenant, membership=membership,
    ).select_related("customer")
    can_manage_work_reports = has_tenant_permission(
        request.user, tenant, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
        membership=membership,
    )
    can_view_inventory = has_tenant_permission(
        request.user, tenant, PermissionCode.INVENTORY_VIEW, membership=membership,
    )
    can_view_customers = has_tenant_permission(
        request.user, tenant, PermissionCode.CUSTOMERS_VIEW, membership=membership,
    )

    context = {
        "is_manager_workspace": membership.base_role in {
            TenantMembership.BaseRole.ADMIN_MANAGER,
            TenantMembership.BaseRole.SUPER_ADMIN,
        },
        "can_view_customers": can_view_customers,
        "can_view_inventory": can_view_inventory,
        "can_manage_work_reports": can_manage_work_reports,
        "customer_count": Customer.objects.filter(tenant=tenant, is_deleted=False).count() if can_view_customers and membership.base_role in {
            TenantMembership.BaseRole.ADMIN_MANAGER, TenantMembership.BaseRole.SUPER_ADMIN,
        } else None,
        "operational_service_count": InternetService.objects.filter(
            tenant=tenant,
        ).exclude(
            operational_status=InternetService.OperationalStatus.DISCONNECTED,
        ).count() if can_view_customers and membership.base_role in {
            TenantMembership.BaseRole.ADMIN_MANAGER, TenantMembership.BaseRole.SUPER_ADMIN,
        } else None,
        "draft_invoice_count": documents.filter(
            document_type=BillingDocument.DocumentType.INVOICE,
            status=BillingDocument.Status.DRAFT,
        ).count(),
        "open_quotation_count": documents.filter(
            document_type=BillingDocument.DocumentType.QUOTATION,
            status__in=[BillingDocument.Status.DRAFT, BillingDocument.Status.SENT],
        ).count(),
        "recent_documents": documents.filter(
            document_type__in=[
                BillingDocument.DocumentType.INVOICE,
                BillingDocument.DocumentType.QUOTATION,
            ],
        )[:8],
    }
    if can_manage_work_reports:
        context["pending_work_report_count"] = TechnicianWorkReport.objects.filter(
            tenant=tenant, status=TechnicianWorkReport.Status.SUBMITTED,
        ).count()
    return render(request, "main_app/workspace_home.html", context)
