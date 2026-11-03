from __future__ import annotations

import logging

from django.db import migrations, transaction


logger = logging.getLogger(__name__)


def backfill_internet_services(apps, schema_editor):
    """Create one deterministic legacy service lineage per proven Internet site.

    Reruns reuse `(tenant, service_code)` and only fill the nullable subscription
    reference. No customer, site, profile, package, subscription, or document
    values are modified.
    """
    db_alias = schema_editor.connection.alias
    Customer = apps.get_model('customers', 'Customer')
    CustomerSite = apps.get_model('customers', 'CustomerSite')
    InternetCustomer = apps.get_model('customers', 'InternetCustomer')
    InternetService = apps.get_model('customers', 'InternetService')
    CustomerSubscription = apps.get_model('billing', 'CustomerSubscription')

    created_count = 0
    reused_count = 0
    linked_count = 0
    skipped_customer_ids = []

    customers = (
        Customer._base_manager.using(db_alias)
        .filter(customer_type='internet')
        .order_by('id')
    )
    for customer in customers.iterator():
        with transaction.atomic(using=db_alias):
            tenant_id = customer.tenant_id or customer.organization_id
            if tenant_id is None:
                raise RuntimeError(f'Internet customer {customer.id} has no tenant.')
            sites = list(
                CustomerSite._base_manager.using(db_alias)
                .filter(customer_id=customer.id)
                .order_by('-is_primary', 'id')
            )
            sites_by_id = {site.id: site for site in sites}
            subscriptions = list(
                CustomerSubscription._base_manager.using(db_alias)
                .filter(customer_id=customer.id)
                .order_by('id')
            )
            proven_site_ids = {subscription.site_id for subscription in subscriptions if subscription.site_id}
            profile_exists = InternetCustomer._base_manager.using(db_alias).filter(customer_id=customer.id).exists()
            primary = next((site for site in sites if site.is_primary), sites[0] if sites else None)
            if primary is not None and (
                profile_exists
                or customer.ip_address
                or customer.vlan_id
                or primary.ip_address
                or primary.vlan_id
            ):
                proven_site_ids.add(primary.id)

            if not proven_site_ids:
                skipped_customer_ids.append(customer.id)
                continue

            for site_id in sorted(proven_site_ids):
                site = sites_by_id.get(site_id)
                if site is None:
                    raise RuntimeError(
                        f'Customer {customer.id} subscription references missing/unowned site {site_id}.'
                    )
                if site.tenant_id != tenant_id or site.customer_id != customer.id:
                    raise RuntimeError(f'Site {site.id} ownership does not match customer {customer.id}.')

                service_code = f'LEGACY-SITE-{site.id}'
                service, created = InternetService._base_manager.using(db_alias).get_or_create(
                    tenant_id=tenant_id,
                    service_code=service_code,
                    defaults={
                        'organization_id': customer.organization_id or tenant_id,
                        'customer_id': customer.id,
                        'site_id': site.id,
                        'name': f'{site.name} Internet Service',
                        'ip_address': site.ip_address or (customer.ip_address if site.is_primary else None),
                        'vlan_id': site.vlan_id or (customer.vlan_id if site.is_primary else None),
                        'operational_status': 'unknown',
                        'technical_notes': 'Created by deterministic legacy backfill; operational status was not inferred from billing state.',
                    },
                )
                if created:
                    created_count += 1
                else:
                    reused_count += 1
                    if service.customer_id != customer.id or service.site_id != site.id:
                        raise RuntimeError(
                            f'Existing service code {service_code} has conflicting ownership.'
                        )

                linked_count += CustomerSubscription._base_manager.using(db_alias).filter(
                    customer_id=customer.id,
                    site_id=site.id,
                    internet_service_id__isnull=True,
                ).update(internet_service_id=service.id)

    logger.info(
        'InternetService backfill created=%s reused=%s subscriptions_linked=%s skipped_customer_ids=%s',
        created_count,
        reused_count,
        linked_count,
        skipped_customer_ids,
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('billing', '0028_service_context_expand'),
    ]

    operations = [
        migrations.RunPython(backfill_internet_services, migrations.RunPython.noop, atomic=False),
    ]
