"""Tenant-bound commercial unit choices; physical review remains mandatory before payment."""
from django.core import signing
from django.core.exceptions import ValidationError
from inventory.packaging_sales import sale_snapshot

SALT = 'billing.commercial-unit.v1'

def unit_token(organization, product, packaging_id):
    return signing.dumps([organization.pk, product.pk, product.packaging_version, packaging_id], salt=SALT)

def unit_options(organization, product):
    if not product or not product.packaging_enabled:
        return []
    choices = [(unit_token(organization, product, None), product.sales_unit.label)]
    for package in product.packaging_definitions.filter(tenant=organization, is_active=True,
            unit__tenant=organization, unit__is_active=True).select_related('unit').order_by('pk')[:100]:
        choices.append((unit_token(organization, product, package.pk),
            f'{package.unit.label} ({package.factor:g} {product.sales_unit.label})'))
    return choices

def resolve_unit(organization, product, token, quantity):
    try:
        tenant_id, product_id, version, packaging_id = signing.loads(token or '', salt=SALT, max_age=86400)
        if tenant_id != organization.pk or product_id != product.pk:
            raise ValueError
    except (signing.BadSignature, TypeError, ValueError):
        raise ValidationError('Choose the selling unit again; its selection is missing, expired or unavailable.')
    return sale_snapshot(organization=organization, product=product, packaging_id=packaging_id,
        quantity=quantity, version=version)
