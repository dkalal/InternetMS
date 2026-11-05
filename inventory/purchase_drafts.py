"""Tenant-authorized, row-locked purchase draft persistence."""
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import DecimalValidator
from django.db import transaction
from .models import Purchase, PurchaseImportSubmission
from .purchase_batch import apply_purchase_batch
from .purchase_import import ImportInputError
from .numbering import PurchaseReferenceNumberService
from .services import audit

def save_purchase_draft(*, form, formset, organization, actor, creating,
                         import_rows=None, import_token=None, import_fingerprint=None, membership=None, support_session=None):
    with transaction.atomic():
        from users.models import TenantMembership
        from users.permissions import PermissionCode, has_tenant_permission, membership_for
        actual = membership_for(actor, organization)
        if support_session is not None:
            from products.packaging import authorize
            actual = authorize(organization=organization, actor=actor, support_session=support_session)
        if not actor.is_active or not has_tenant_permission(actor, organization, PermissionCode.PURCHASES_CREATE, membership=actual):
            raise PermissionDenied('Purchase creation permission is required.')
        if not creating:
            current = Purchase.objects.unscoped().select_for_update().filter(pk=form.instance.pk, tenant=organization).first()
            if current is None or (current.created_by_id != actor.pk and not has_tenant_permission(actor, organization, PermissionCode.PURCHASE_VIEW, membership=actual)):
                raise PermissionDenied('Purchase draft unavailable in this tenant.')
            if current.status != Purchase.Status.DRAFT:
                raise ValidationError('The purchase was already received or cancelled. Reload its immutable history.')
        if import_rows:
            type(organization).objects.select_for_update().get(pk=organization.pk)
            previous = PurchaseImportSubmission.objects.filter(tenant=organization, token=import_token).first()
            if previous:
                if previous.fingerprint != import_fingerprint:
                    raise ImportInputError('This import token was already used with different content. Review and apply a new batch.')
                return previous.purchase
        purchase = form.save(commit=False)
        purchase.organization = purchase.tenant = organization
        if creating:
            purchase.created_by = actor
            if form.cleaned_data['auto_generated_reference'] == purchase.reference_number:
                purchase.reference_number = PurchaseReferenceNumberService.next_number(
                    organization=organization,
                    purchase_date=purchase.purchase_date,
                )
        purchase.save()
        formset.instance = purchase
        formset.save()
        from products.models import Product
        from .packaging_receipts import validate_receipt_line
        product_ids = purchase.lines.values_list('product_id', flat=True)
        products = {p.pk:p for p in Product.objects.unscoped().select_for_update().filter(tenant=organization, pk__in=product_ids).order_by('pk')}
        for line in purchase.lines.all():
            if line.product_id not in products:
                raise PermissionDenied('Cross-tenant purchase product denied.')
            if products[line.product_id].packaging_enabled:
                validate_receipt_line(organization=organization, line=line, product=products[line.product_id])
        # The workspace submits the complete proposal snapshot on edit.
        purchase.product_proposals.filter(tenant=organization).delete()
        if import_rows:
            apply_purchase_batch(purchase=purchase, rows=import_rows, organization=organization,
                                 actor=actor, membership=membership)
        from .purchase_batch import proposal_lines
        total = sum((line.line_total for line in list(purchase.lines.all()) + proposal_lines(purchase)), Decimal('0.00'))
        total = total.quantize(Decimal('0.01'))
        DecimalValidator(14, 2)(total)
        Purchase.objects.filter(tenant=organization, pk=purchase.pk).update(total_cost=total)
        purchase.total_cost = total
        audit(
            organization=organization,
            actor=actor,
            action='inventory.purchase.created' if creating else 'inventory.purchase.updated',
            obj=purchase,
            new_value={'reference_number': purchase.reference_number, 'total_cost': str(total)},
        )
        if import_rows:
            PurchaseImportSubmission.objects.create(
                organization=organization, tenant=organization, token=import_token,
                fingerprint=import_fingerprint, purchase=purchase,
            )
    return purchase
