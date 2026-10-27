"""Concurrency-safe persistence for catalog products created from purchasing."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.text import slugify

from .models import Product


def save_purchase_product(product, *, organization, supplied_sku='', max_attempts=20):
    """Allocate a SKU at persistence, using a savepoint for each retry."""
    base = (slugify(product.name).replace('-', '')[:24] or 'ITEM').upper()
    supplied_sku = supplied_sku.strip().upper()
    product.organization = product.tenant = organization
    for attempt in range(1, max_attempts + 1):
        product.sku = supplied_sku or (base if attempt == 1 else f'{base}-{attempt}')
        if Product.objects.unscoped().filter(tenant=organization, sku__iexact=product.sku).exists():
            if supplied_sku:
                raise ValidationError({'sku': 'SKU is already in use.'})
            continue
        try:
            with transaction.atomic():
                product.save()
            return product
        except (IntegrityError, ValidationError) as exc:
            collision = Product.objects.unscoped().filter(tenant=organization, sku__iexact=product.sku).exists()
            if supplied_sku or not collision:
                raise ValidationError({'sku': 'SKU is already in use or the product could not be saved.'}) from exc
    raise ValidationError({'sku': 'Could not allocate a unique SKU. Try again.'})
