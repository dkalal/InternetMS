from __future__ import annotations

import logging

from django.http import HttpRequest
from django.db import models, transaction
from rest_framework.throttling import UserRateThrottle

from audit.models import AuditLog
from customers.models import Customer

from .models import ExternalAssetReference, IntegrationConsumer


logger = logging.getLogger('integrations.api')


class IntegrationBurstThrottle(UserRateThrottle):
    scope = 'integration_burst'


class IntegrationSustainedThrottle(UserRateThrottle):
    scope = 'integration_sustained'


def resolve_integration_consumer(request: HttpRequest) -> IntegrationConsumer | None:
    cached = getattr(request, '_integration_consumer', None)
    if cached is not None:
        return cached

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None

    consumer = (
        IntegrationConsumer.objects.select_related('organization', 'user')
        .filter(user=user, is_active=True)
        .first()
    )
    if consumer is not None:
        request._integration_consumer = consumer
    return consumer


def build_customer_queryset(consumer: IntegrationConsumer, *, customer_uuid=None):
    if consumer is None:
        return Customer.objects.none()
    queryset = (
        Customer.objects.for_organization(consumer.organization)
        .active()
        .filter(is_deleted=False)
        .order_by('-created_at', '-id')
    )
    if customer_uuid is not None:
        queryset = queryset.filter(uuid=customer_uuid)
    return queryset


def build_customer_asset_target_queryset(consumer: IntegrationConsumer, *, customer_uuid=None):
    """Customers eligible for asset projection updates, including inactive accounts."""
    if consumer is None:
        return Customer.objects.none()
    queryset = Customer.objects.for_organization(consumer.organization).filter(is_deleted=False)
    if customer_uuid is not None:
        queryset = queryset.filter(uuid=customer_uuid)
    return queryset


@transaction.atomic
def replace_customer_asset_snapshot(*, consumer, customer, assets):
    """Idempotently replace one customer's AssetMS projection within the consumer tenant."""
    tenant = consumer.organization
    incoming_ids = {asset['external_uuid'] for asset in assets}
    existing = {
        row.external_uuid: row
        for row in ExternalAssetReference.objects.select_for_update().filter(tenant=tenant).filter(
            models.Q(customer=customer) | models.Q(external_uuid__in=incoming_ids)
        )
    }
    created = updated = 0
    update_fields = (
        'display_name', 'asset_tag', 'serial_number', 'category_name', 'branch_name',
        'status', 'description', 'custom_attributes', 'source_url', 'source_updated_at',
    )
    for payload in assets:
        reference = existing.get(payload['external_uuid'])
        if reference is None:
            ExternalAssetReference.objects.create(
                tenant=tenant,
                organization=tenant,
                customer=customer,
                **payload,
            )
            created += 1
            continue
        changed = False
        if reference.customer_id != customer.id:
            reference.customer = customer
            changed = True
        for field in update_fields:
            if getattr(reference, field) != payload[field]:
                setattr(reference, field, payload[field])
                changed = True
        if changed:
            reference.save(update_fields=['customer', *update_fields, 'last_synced_at'])
            updated += 1
    stale = ExternalAssetReference.objects.filter(tenant=tenant, customer=customer)
    if incoming_ids:
        stale = stale.exclude(external_uuid__in=incoming_ids)
    deleted, _ = stale.delete()
    return {'created': created, 'updated': updated, 'deleted': deleted, 'total': len(assets)}


def apply_customer_search(queryset, search_term: str | None):
    if not search_term:
        return queryset
    return queryset.filter(
        models.Q(name__icontains=search_term)
        | models.Q(phone__icontains=search_term)
        | models.Q(email__icontains=search_term)
    )


def log_customer_api_access(
    *,
    request: HttpRequest,
    consumer: IntegrationConsumer | None,
    action: str,
    status_code: int,
    record_count: int | None = None,
    customer_uuid=None,
):
    user = getattr(request, 'user', None)
    metadata = {
        'path': request.path,
        'method': request.method,
        'status_code': status_code,
        'record_count': record_count,
        'customer_uuid': str(customer_uuid) if customer_uuid else '',
        'consumer_name': consumer.name if consumer else '',
    }

    logger.info(
        'integration_api_access consumer=%s tenant=%s path=%s status=%s record_count=%s',
        consumer.name if consumer else 'unknown',
        consumer.organization.slug if consumer else 'unknown',
        request.path,
        status_code,
        record_count if record_count is not None else '',
    )

    if consumer is None:
        return

    AuditLog.objects.create(
        organization=consumer.organization,
        tenant=consumer.organization,
        actor=user if getattr(user, 'is_authenticated', False) else None,
        action=action,
        object_type='IntegrationConsumer',
        object_id=str(consumer.id),
        metadata=metadata,
    )
