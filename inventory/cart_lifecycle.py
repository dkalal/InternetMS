"""Cart lifecycle policy. Maintenance is deliberately explicit and tenant-scoped."""
from datetime import timedelta
from functools import wraps

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from users.permissions import PermissionCode, has_tenant_permission, sales_document_queryset_for
from .models import Cart, InventorySale


def visible_carts(*, organization, actor, membership=None):
    qs = Cart.objects.unscoped().filter(tenant=organization)
    if not has_tenant_permission(actor, organization, PermissionCode.SALES_DOCUMENTS_CREATE, membership=membership):
        return qs.none()
    if not has_tenant_permission(actor, organization, PermissionCode.FINANCE_SALES_VIEW_ALL, membership=membership):
        from django.db.models import Q
        docs = sales_document_queryset_for(actor, organization, membership=membership).values('pk')
        qs = qs.filter(created_by=actor).filter(
            Q(invoice__isnull=True) | Q(invoice_id__in=docs),
        ).filter(Q(quotation__isnull=True) | Q(quotation_id__in=docs))
    return qs


def business_linked(cart):
    return bool(cart.quotation_id or cart.invoice_id or InventorySale.objects.unscoped().filter(cart=cart).exists())


def genuinely_empty(cart):
    return not (
        cart.customer_id or cart.walk_in_name.strip() or cart.shipping_address.strip()
        or cart.notes.strip() or cart.discount_amount or cart.tax_rate
        or cart.sale_pricing_category != Cart.SalePricingCategory.CUSTOMER_TIER
        or cart.converted_at or cart.abandoned_at or cart.restored_at
        or cart.abandonment_reason.strip() or cart.lines.exists() or business_linked(cart)
    )


def stale(cart, *, now=None):
    return cart.status == Cart.Status.DRAFT and cart.updated_at <= (now or timezone.now()) - timedelta(days=7)


def cart_access(view):
    """Authorize every cart endpoint and serialize POSTs before reading forms/lines.

    All normal mutations and cleanup lock the parent before inspecting children.
    Converted checkout retries may read the cart but must not save form values.
    """
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        from users.tenancy import require_organization
        from users.permissions import require_permission
        organization = require_organization(request)
        require_permission(request, PermissionCode.SALES_DOCUMENTS_CREATE)
        def check(lock=False):
            qs = visible_carts(organization=organization, actor=request.user, membership=request.membership)
            if lock:
                qs = qs.select_for_update()
            return get_object_or_404(qs, pk=kwargs.get('cart_pk', kwargs.get('pk')))
        if request.method == 'POST':
            with transaction.atomic():
                check(lock=True)
                return view(request, *args, **kwargs)
        check()
        return view(request, *args, **kwargs)
    return wrapped


@transaction.atomic
def cleanup_candidate(*, cart_id, now, apply=False):
    """Recheck under the same parent lock used by interactive cart mutations."""
    from .services import audit
    cart = Cart.objects.unscoped().select_for_update().filter(pk=cart_id).first()
    if cart is None or cart.status != Cart.Status.DRAFT:
        return 'skipped', 'missing_or_not_draft'
    if business_linked(cart) or cart.converted_at:
        return 'skipped', 'business_link_review'
    if cart.updated_at > now - timedelta(hours=24):
        return 'skipped', 'recent_activity'
    empty = genuinely_empty(cart)
    if empty:
        # Cart has only lines (CASCADE) and a sale (PROTECT) as reverse business
        # relations. No lines/sale may exist here; audits use scalar object IDs.
        if apply:
            audit(organization=cart.tenant, actor=None, action='inventory.cart.empty_deleted', obj=cart,
                  metadata={'policy': 'empty_24_hours', 'stock_changed': False})
            cart.delete()
        return 'deleted' if apply else 'eligible', 'empty_24_hours'
    if cart.updated_at > now - timedelta(days=30):
        return 'skipped', 'nonempty_under_30_days'
    if apply:
        cart.status = Cart.Status.ABANDONED
        cart.abandoned_at = now
        cart.abandoned_by = None
        cart.abandonment_reason = 'Automatic: inactive for at least 30 days.'
        # Automatic policy changes are not meaningful user activity.
        cart.save(update_fields=['status', 'abandoned_at', 'abandoned_by', 'abandonment_reason'])
        audit(organization=cart.tenant, actor=None, action='inventory.cart.auto_abandoned', obj=cart,
              old_value={'status': Cart.Status.DRAFT}, new_value={'status': Cart.Status.ABANDONED},
              metadata={'policy': 'inactive_30_days', 'stock_changed': False})
    return 'abandoned' if apply else 'eligible', 'inactive_30_days'
