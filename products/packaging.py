"""Server-authoritative package configuration and exact quantity validation."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max

from users.models import SupportAccessSession, TenantMembership
from users.permissions import PermissionCode, has_tenant_permission, membership_for
from .models import Product, ProductPackaging, UnitOfMeasure
from .pricing import cost_floor_for


class PackagingError(ValidationError):
    pass


def workflows_ready():
    # Deliberately not an environment setting. Later verified phases replace this
    # gate only after every inventory/billing entry point understands snapshots.
    return False


def whole_quantity(value, *, label='Quantity', allow_zero=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise PackagingError(f'{label} must be a finite whole number.')
    if (not number.is_finite() or number != number.to_integral_value()
            or number < 0 or (number == 0 and not allow_zero) or number >= Decimal('10000000000')):
        raise PackagingError(f'{label} must be a {"nonnegative" if allow_zero else "positive"} whole number within the supported range.')
    return number.quantize(Decimal('0.000001'))


def authorize(*, organization, actor, stock=False, support_session=None):
    if not actor or not actor.is_authenticated or not actor.is_active or not organization.is_active:
        raise PermissionDenied('An active tenant membership is required.')
    membership = membership_for(actor, organization)
    if support_session is not None:
        support = SupportAccessSession.objects.filter(pk=support_session.pk, actor=actor, tenant=organization, ended_at__isnull=True).first()
        if support is None or not support.reason.strip() or not support.session_key:
            raise PermissionDenied('An active audited support session is required.')
        membership = TenantMembership.objects.filter(user=actor, is_active=True, tenant__is_active=True, base_role=TenantMembership.BaseRole.SUPER_ADMIN).first()
    required = [PermissionCode.PRODUCTS_UPDATE, PermissionCode.COST_REPORT_VIEW]
    if stock:
        required.append(PermissionCode.INVENTORY_ADJUST)
    if membership is None or any(not has_tenant_permission(actor, organization, code, membership=membership) for code in required):
        raise PermissionDenied('Packaging configuration requires product, cost and applicable stock permissions.')
    if membership.base_role == TenantMembership.BaseRole.SUPER_ADMIN:
        if not support_session or not SupportAccessSession.objects.filter(
            pk=support_session.pk, actor=actor, tenant=organization, ended_at__isnull=True,
        ).exclude(reason='').exclude(session_key='').exists():
            raise PermissionDenied('An active audited support session is required.')
    return membership


def identifier(value, *, label='Identifier'):
    text = str(value)
    if not text.isascii() or not text.isdecimal() or len(text) > 18 or int(text) <= 0:
        raise PackagingError(f'{label} must be a valid positive identifier.')
    return int(text)


def locked_product(*, organization, product_id):
    product_id = identifier(product_id, label="Product")
    product = Product.objects.unscoped().select_for_update().filter(pk=product_id, tenant=organization).first()
    if product is None or product.organization_id != organization.pk:
        raise PermissionDenied('Product unavailable in the active tenant.')
    if product.item_type != Product.ItemType.PHYSICAL or not product.track_stock or product.is_serialized:
        raise PackagingError('Use a non-serialized stock-tracked physical product for divisible packaging.')
    if not product.sales_unit_id or not product.sales_unit.is_active or product.sales_unit.tenant_id != organization.pk:
        raise PackagingError('Select an active base inventory unit before configuring packaging.')
    return product


def validated_prices(*, product, factor, prices):
    allowed = {'standard_price', 'technician_price', 'wholesale_price', 'retail_price'}
    if not isinstance(prices, dict) or set(prices) - allowed or 'standard_price' not in prices:
        raise PackagingError('Supply the independent standard package price and supported tier prices only.')
    floor = cost_floor_for(product)
    parsed = {}
    for field, value in prices.items():
        if value is None and field != 'standard_price':
            parsed[field] = None
            continue
        try:
            value = Decimal(str(value))
            if not value.is_finite() or value.as_tuple().exponent < -2 or value >= Decimal('10000000000'):
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            raise PackagingError('Package prices must be finite amounts with at most two decimal places.')
        if value / factor <= floor:
            raise PackagingError('Each configured package selling price must be strictly above the applicable inventory cost.')
        parsed[field] = value
    return parsed


class PackagingService:
    @staticmethod
    @transaction.atomic
    def configure(*, organization, actor, product_id, unit_id, factor, prices, support_session=None):
        authorize(organization=organization, actor=actor, support_session=support_session)
        product = locked_product(organization=organization, product_id=product_id)
        unit_id = identifier(unit_id, label="Packaging unit")
        unit = UnitOfMeasure.objects.unscoped().filter(pk=unit_id, tenant=organization, is_active=True).first()
        if unit is None:
            raise PermissionDenied('Packaging unit unavailable in the active tenant.')
        factor = whole_quantity(factor, label='Conversion factor')
        parsed = validated_prices(product=product, factor=factor, prices=prices)
        revision = (ProductPackaging.objects.unscoped().filter(product=product).aggregate(n=Max('version'))['n'] or 0) + 1
        package = ProductPackaging(tenant=organization, product=product, unit=unit, factor=factor, version=revision, **parsed)
        package.save()
        Product.objects.unscoped().filter(pk=product.pk, tenant=organization).update(packaging_version=product.packaging_version + 1)
        from inventory.services import audit
        audit(organization=organization, actor=actor, action='products.packaging.configured', obj=package,
              new_value={'product_id': product.pk, 'unit_id': unit.pk, 'factor': str(factor), 'revision': revision})
        return package

    @staticmethod
    @transaction.atomic
    def retire(*, organization, actor, product_id, packaging_id, support_session=None):
        authorize(organization=organization, actor=actor, support_session=support_session)
        product = locked_product(organization=organization, product_id=product_id)
        package = ProductPackaging.objects.unscoped().filter(pk=identifier(packaging_id), product=product, tenant=organization).first()
        if package is None:
            raise PermissionDenied('Packaging definition unavailable in the active tenant.')
        if not package.is_active:
            return package
        ProductPackaging.objects.unscoped().filter(pk=package.pk, tenant=organization).update(is_active=False)
        Product.objects.unscoped().filter(pk=product.pk, tenant=organization).update(packaging_version=product.packaging_version + 1)
        package.is_active = False
        from inventory.services import audit
        audit(organization=organization, actor=actor, action='products.packaging.retired', obj=package,
              new_value={'product_id': product.pk, 'revision': package.version, 'is_active': False})
        return package

    @staticmethod
    @transaction.atomic
    def update_prices(*, organization, actor, product_id, packaging_id, prices, support_session=None):
        authorize(organization=organization, actor=actor, support_session=support_session)
        product = locked_product(organization=organization, product_id=product_id)
        package = ProductPackaging.objects.unscoped().filter(pk=identifier(packaging_id), tenant=organization, product=product, is_active=True).first()
        if package is None:
            raise PermissionDenied('Active packaging definition unavailable in this tenant.')
        fields = ('standard_price', 'technician_price', 'wholesale_price', 'retail_price')
        if not isinstance(prices, dict) or set(prices) - set(fields):
            raise PackagingError('Supply supported independent package prices only.')
        old = {field: getattr(package, field) for field in fields}
        parsed = validated_prices(product=product, factor=package.factor, prices={**old, **prices})
        if parsed == old:
            return package
        for field, value in parsed.items():
            setattr(package, field, value)
        package._packaging_service_update = True
        package.save(update_fields=list(fields))
        Product.objects.unscoped().filter(pk=product.pk, tenant=organization).update(packaging_version=product.packaging_version + 1)
        from inventory.services import audit
        audit(organization=organization, actor=actor, action='products.packaging.prices_updated', obj=package,
              old_value={key: str(value) if value is not None else None for key, value in old.items()},
              new_value={key: str(value) if value is not None else None for key, value in parsed.items()})
        return package
