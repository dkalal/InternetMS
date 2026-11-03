"""The sole SKU allocator; called only at final business persistence."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Upper, Trim

from .models import Product, ProductSKUCounter
from .sku import MAX_NUMBER, generated_number, validate_manual_sku


def occupied_max(tenant):
    """One initialization/recovery scan, never a scan for each allocation."""
    maximum = 0
    codes = Product.objects.unscoped().filter(tenant=tenant).annotate(normalized=Upper(Trim('sku'))).filter(
        normalized__startswith='PRD-').values_list('sku', flat=True)
    for code in codes.iterator():
        number = generated_number(code)
        if number is not None:
            maximum = max(maximum, number)
    return maximum


def locked_counter(organization):
    # get_or_create resolves concurrent first inserts using its savepoint and
    # the unique tenant key; lock the resulting persisted row afterwards.
    counter, created = ProductSKUCounter.objects.get_or_create(tenant=organization)
    counter = ProductSKUCounter.objects.select_for_update().get(pk=organization.pk)
    if created:
        counter.next_number = occupied_max(organization) + 1
    return counter


def sku_conflict(exc):
    if isinstance(exc, ValidationError):
        groups = exc.error_dict.values() if hasattr(exc, 'error_dict') else [exc.error_list]
        errors = [error for group in groups for error in group]
        known_messages = {constraint.get_violation_error_message() for constraint in Product._meta.constraints
                          if constraint.name in {'uniq_product_sku_per_tenant', 'uniq_product_sku_normalized'}}
        # Product has only SKU unique constraints. Never retry a mixed field /
        # uniqueness error or translate a price/category/unit failure as SKU.
        return bool(errors) and all(error.code in {'sku_conflict', 'unique_together'}
            or (error.code is None and error.message in known_messages) for error in errors)
    return getattr(getattr(exc.__cause__, 'diag', None), 'constraint_name', None) in {
        'uniq_product_sku_per_tenant', 'uniq_product_sku_normalized',
    }


@transaction.atomic
def save_purchase_product(product, *, organization, supplied_sku='', max_attempts=None, actor=None, _backfill=False):
    """Shared final creation, compatible with enclosing import transactions.

    actor=None is the trusted internal alternative for existing aggregate
    owners which already authorize and audit. max_attempts is compatibility
    only; allocation uses at most one recovery scan, never a candidate loop.
    """
    if organization is None or not organization.pk:
        raise ValidationError({'tenant': 'An active business is required.'})
    # Match the aggregate owners' parent-before-counter lock order. Without
    # this, a direct creator can hold the counter while its deferred tenant FK
    # waits on a batch's organization lock, and the batch waits on the counter.
    type(organization).objects.select_for_update(no_key=True).get(pk=organization.pk)
    if (product.pk or not product._state.adding) and not _backfill:
        raise ValidationError('Use the update workflow for existing products.')
    if _backfill:
        existing = Product.objects.unscoped().select_for_update().get(pk=product.pk, tenant=organization)
        if existing.sku or existing.has_unit_history():
            raise ValidationError('Only an unused legacy blank SKU may be backfilled; review historical dependencies separately.')
    if any(value and value != organization.pk for value in (product.tenant_id, product.organization_id)):
        raise PermissionDenied('Product must belong to the active business.')
    if not _backfill and (product.quantity or product.stock):
        raise ValidationError('New catalog products must start with zero stock; use receiving or an authorized adjustment.')
    if actor is not None:
        from users.permissions import PermissionCode, has_tenant_permission
        if not has_tenant_permission(actor, organization, PermissionCode.PRODUCTS_CREATE):
            raise PermissionDenied('Product creation permission is required.')
    product.organization = product.tenant = organization
    supplied_sku = validate_manual_sku(supplied_sku)
    counter = None if supplied_sku else locked_counter(organization)
    for recovery in range(2):
        if counter and counter.next_number > MAX_NUMBER:
            raise ValidationError({'sku': 'Product sequence is exhausted. Contact support.'})
        product.sku = supplied_sku or f'PRD-{counter.next_number:06d}'
        collision = Product.objects.unscoped().filter(tenant=organization).annotate(
            normalized=Upper(Trim('sku')),
        ).filter(normalized=product.sku).exists()
        if collision:
            if not counter:
                raise ValidationError({'sku': 'SKU is already in use in this business.'})
            if recovery:
                break
            counter.next_number = max(counter.next_number, occupied_max(organization) + 1)
            continue
        try:
            with transaction.atomic():
                product.save()
        except (IntegrityError, ValidationError) as exc:
            if not sku_conflict(exc):
                raise
            if not counter:
                raise ValidationError({'sku': 'SKU is already in use in this business.'}) from exc
            if not _backfill:
                product.pk = None
                product._state.adding = True
            counter.next_number = max(counter.next_number + 1, occupied_max(organization) + 1)
            continue
        if counter:
            counter.next_number += 1
            counter.save(update_fields=['next_number'])
        if actor is not None:
            from inventory.services import audit
            audit(organization=organization, actor=actor, action=('inventory.product.sku_backfilled' if _backfill else 'inventory.product.created'), obj=product,
                  new_value={'sku': product.sku, 'name': product.name})
        return product
    raise ValidationError({'sku': 'SKU allocation state changed repeatedly. Contact support.'})
