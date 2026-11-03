from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from billing.models import BillingDocument, BillingLineItem, CustomerSubscription, SubscriptionPeriod
from services.models import Package

from .models import Customer, CustomerSite, InternetCustomer, InternetService


@dataclass
class FindingBucket:
    severity: str
    description: str
    count: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)

    def add(self, record: dict[str, Any], *, sample_limit: int) -> None:
        self.count += 1
        if len(self.records) < sample_limit:
            self.records.append(record)

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "description": self.description,
            "count": self.count,
            "records": self.records,
        }


FINDING_DEFINITIONS = {
    "internet_customer_without_subscription": (
        "review",
        "Internet customer has no subscription history; creating a service is possible, but no commercial agreement can be inferred.",
    ),
    "customer_with_multiple_subscriptions": (
        "review",
        "Customer has multiple historical or current subscriptions; topology must be resolved through site/service ownership.",
    ),
    "site_with_multiple_active_subscriptions": (
        "blocking",
        "A site has multiple active subscriptions that are not resolved to distinct connection identities.",
    ),
    "duplicate_active_subscription_relationship": (
        "blocking",
        "More than one active subscription exists for the same tenant/customer/site/package relationship.",
    ),
    "invalid_subscription_dates": (
        "blocking",
        "Subscription end date is earlier than its start date, or its required start date is missing.",
    ),
    "invalid_profile_dates": (
        "blocking",
        "Legacy Internet-profile end date is earlier than its start date.",
    ),
    "profile_subscription_date_conflict": (
        "blocking",
        "Legacy Internet-profile and deterministically matched subscription dates disagree.",
    ),
    "profile_package_type_conflict": (
        "blocking",
        "Legacy Internet connection classification disagrees with one or more active package classifications.",
    ),
    "missing_profile_package_type": (
        "review",
        "Legacy Internet profile has no connection classification. Package type remains available canonically from Package and is not inferred back into the profile.",
    ),
    "internet_customer_without_site": (
        "blocking",
        "Internet customer has no site to own a connection.",
    ),
    "customer_without_primary_site": (
        "blocking",
        "Customer has sites but none is primary; deterministic compatibility reads are unsafe.",
    ),
    "legacy_primary_site_network_conflict": (
        "blocking",
        "Customer-level and primary-site IP/VLAN values disagree.",
    ),
    "legacy_primary_site_location_conflict": (
        "review",
        "Customer-level and primary-site location/address values disagree and may represent account versus service-location edits.",
    ),
    "primary_package_assignment_conflict": (
        "blocking",
        "Customer and primary-site package assignments disagree.",
    ),
    "subscription_without_site": (
        "blocking",
        "Subscription is not linked to a site.",
    ),
    "subscription_without_service": (
        "review",
        "An existing subscription was not linked to an Internet service by the deterministic backfill.",
    ),
    "service_without_subscription": (
        "review",
        "An installed-service record has no commercial subscription history; this is valid only where profile/network evidence proved the service.",
    ),
    "tenant_mismatch": (
        "blocking",
        "A tenant-owned relation contains inconsistent organization, tenant, customer, site, package, document, or line ownership.",
    ),
    "duplicate_ip_across_customers": (
        "review",
        "The same non-empty IP appears on more than one customer account; uniqueness is not assumed, but migration needs review.",
    ),
    "duplicate_vlan_across_customers": (
        "review",
        "The same non-empty VLAN appears on more than one customer account; VLAN reuse may be valid and is not treated as an error.",
    ),
    "walk_in_with_service_topology": (
        "review",
        "Walk-in/random customer already has site, package, network, profile, or subscription data; it must not be silently removed.",
    ),
    "financial_document_customer_mismatch": (
        "blocking",
        "Financial document customer ownership does not match its tenant or related invoice.",
    ),
    "receipt_without_invoice": (
        "blocking",
        "Receipt has no source invoice from which customer and future site/service context can be derived.",
    ),
    "subscription_period_document_mismatch": (
        "blocking",
        "Subscription-period invoice or receipt ownership disagrees with the subscription customer/tenant.",
    ),
}


def _serialize(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return value


def build_domain_audit(*, tenant_id: int | None = None, sample_limit: int = 100) -> dict[str, Any]:
    """Inspect ownership without writing data or audit rows."""
    buckets = {
        code: FindingBucket(severity=definition[0], description=definition[1])
        for code, definition in FINDING_DEFINITIONS.items()
    }

    def add(code: str, **record: Any) -> None:
        buckets[code].add(
            {key: _serialize(value) for key, value in record.items()},
            sample_limit=sample_limit,
        )

    customers = Customer._base_manager.all().order_by("id")
    sites = CustomerSite._base_manager.select_related("customer").all().order_by("id")
    services = InternetService._base_manager.select_related("customer", "site").all().order_by("id")
    profiles = InternetCustomer._base_manager.select_related("customer").all().order_by("id")
    subscriptions = CustomerSubscription._base_manager.select_related(
        "customer", "site", "package", "internet_service"
    ).all().order_by("id")
    packages = Package._base_manager.all().order_by("id")
    documents = BillingDocument._base_manager.select_related("customer", "site", "invoice").all().order_by("id")
    lines = BillingLineItem._base_manager.select_related("document", "product", "package", "internet_service", "subscription").all().order_by("id")
    periods = SubscriptionPeriod._base_manager.select_related(
        "subscription", "subscription__customer", "invoice", "receipt"
    ).all().order_by("id")

    if tenant_id is not None:
        customers = customers.filter(tenant_id=tenant_id)
        sites = sites.filter(tenant_id=tenant_id)
        services = services.filter(tenant_id=tenant_id)
        profiles = profiles.filter(tenant_id=tenant_id)
        subscriptions = subscriptions.filter(tenant_id=tenant_id)
        packages = packages.filter(tenant_id=tenant_id)
        documents = documents.filter(tenant_id=tenant_id)
        lines = lines.filter(tenant_id=tenant_id)
        periods = periods.filter(tenant_id=tenant_id)

    customer_rows = list(customers)
    site_rows = list(sites)
    service_rows = list(services)
    profile_rows = list(profiles)
    subscription_rows = list(subscriptions)
    package_rows = list(packages)
    document_rows = list(documents)
    line_rows = list(lines)
    period_rows = list(periods)

    sites_by_customer: dict[int, list[CustomerSite]] = defaultdict(list)
    profiles_by_customer = {profile.customer_id: profile for profile in profile_rows}
    subscriptions_by_customer: dict[int, list[CustomerSubscription]] = defaultdict(list)
    active_by_site: dict[int | None, list[CustomerSubscription]] = defaultdict(list)
    for site in site_rows:
        sites_by_customer[site.customer_id].append(site)
    for subscription in subscription_rows:
        subscriptions_by_customer[subscription.customer_id].append(subscription)
        if subscription.status == CustomerSubscription.Status.ACTIVE:
            active_by_site[subscription.site_id].append(subscription)

    for customer in customer_rows:
        customer_sites = sites_by_customer.get(customer.id, [])
        customer_subscriptions = subscriptions_by_customer.get(customer.id, [])
        profile = profiles_by_customer.get(customer.id)
        primary_sites = [site for site in customer_sites if site.is_primary]
        primary = primary_sites[0] if len(primary_sites) == 1 else None

        if customer.organization_id != customer.tenant_id or customer.tenant_id is None:
            add("tenant_mismatch", object_type="Customer", object_id=customer.id, organization_id=customer.organization_id, tenant_id=customer.tenant_id)
        if customer.customer_type == "internet" and not customer_subscriptions:
            add("internet_customer_without_subscription", customer_id=customer.id)
        if customer.customer_type == "internet" and not customer_sites:
            add("internet_customer_without_site", customer_id=customer.id)
        if customer_sites and len(primary_sites) != 1:
            add("customer_without_primary_site", customer_id=customer.id, site_ids=[site.id for site in customer_sites], primary_site_ids=[site.id for site in primary_sites])
        if len(customer_subscriptions) > 1:
            add("customer_with_multiple_subscriptions", customer_id=customer.id, subscription_ids=[item.id for item in customer_subscriptions], active_subscription_ids=[item.id for item in customer_subscriptions if item.status == CustomerSubscription.Status.ACTIVE])

        if primary is not None:
            network_conflicts = {}
            for field_name in ("ip_address", "vlan_id"):
                legacy_value = getattr(customer, field_name)
                site_value = getattr(primary, field_name)
                if legacy_value and site_value and str(legacy_value) != str(site_value):
                    network_conflicts[field_name] = {"customer": str(legacy_value), "site": str(site_value)}
            if network_conflicts:
                add("legacy_primary_site_network_conflict", customer_id=customer.id, site_id=primary.id, conflicts=network_conflicts)

            location_conflicts = {}
            for field_name in ("location", "address"):
                legacy_value = getattr(customer, field_name) or ""
                site_value = getattr(primary, field_name) or ""
                if legacy_value and site_value and legacy_value.strip() != site_value.strip():
                    location_conflicts[field_name] = {"customer": legacy_value, "site": site_value}
            if location_conflicts:
                add("legacy_primary_site_location_conflict", customer_id=customer.id, site_id=primary.id, conflicts=location_conflicts)

            customer_package_ids = set(customer.packages.values_list("id", flat=True))
            site_package_ids = set(primary.packages.values_list("id", flat=True))
            if customer_package_ids != site_package_ids:
                add("primary_package_assignment_conflict", customer_id=customer.id, site_id=primary.id, customer_package_ids=sorted(customer_package_ids), site_package_ids=sorted(site_package_ids))

        if profile is not None:
            if profile.start_date and profile.end_date and profile.end_date < profile.start_date:
                add("invalid_profile_dates", profile_id=profile.id, customer_id=customer.id, start_date=profile.start_date, end_date=profile.end_date)
            deterministic = [sub for sub in customer_subscriptions if sub.status == CustomerSubscription.Status.ACTIVE]
            if len(deterministic) != 1 and len(customer_subscriptions) == 1:
                deterministic = customer_subscriptions
            if len(deterministic) == 1:
                subscription = deterministic[0]
                conflicts = {}
                if profile.start_date and subscription.start_date and profile.start_date != subscription.start_date:
                    conflicts["start_date"] = {"profile": profile.start_date.isoformat(), "subscription": subscription.start_date.isoformat()}
                if profile.end_date and subscription.end_date and profile.end_date != subscription.end_date:
                    conflicts["end_date"] = {"profile": profile.end_date.isoformat(), "subscription": subscription.end_date.isoformat()}
                if conflicts:
                    add("profile_subscription_date_conflict", customer_id=customer.id, profile_id=profile.id, subscription_id=subscription.id, conflicts=conflicts)

            active_types = sorted({sub.package.package_type for sub in customer_subscriptions if sub.status == CustomerSubscription.Status.ACTIVE})
            if not profile.package_type:
                add("missing_profile_package_type", customer_id=customer.id, profile_id=profile.id, active_package_types=active_types)
            elif active_types and any(package_type != profile.package_type for package_type in active_types):
                add("profile_package_type_conflict", customer_id=customer.id, profile_id=profile.id, profile_package_type=profile.package_type, active_package_types=active_types)

        if customer.customer_type == "random":
            has_topology = bool(customer_sites or customer_subscriptions or profile or customer.ip_address or customer.vlan_id or customer.packages.exists())
            if has_topology:
                add("walk_in_with_service_topology", customer_id=customer.id, site_ids=[site.id for site in customer_sites], subscription_ids=[sub.id for sub in customer_subscriptions], has_profile=profile is not None)

    exact_active = Counter(
        (sub.tenant_id, sub.customer_id, sub.site_id, sub.package_id, sub.internet_service_id)
        for sub in subscription_rows
        if sub.status == CustomerSubscription.Status.ACTIVE
    )
    for relationship, count in sorted(exact_active.items(), key=lambda item: str(item[0])):
        if count > 1:
            tenant, customer, site, package, internet_service = relationship
            add("duplicate_active_subscription_relationship", tenant_id=tenant, customer_id=customer, site_id=site, package_id=package, internet_service_id=internet_service, count=count)

    for site_id, active in sorted(active_by_site.items(), key=lambda item: (item[0] is None, item[0] or 0)):
        service_ids = [sub.internet_service_id for sub in active]
        if site_id is not None and len(active) > 1 and (None in service_ids or len(set(service_ids)) != len(service_ids)):
            add("site_with_multiple_active_subscriptions", site_id=site_id, customer_id=active[0].customer_id, subscription_ids=[sub.id for sub in active], package_ids=[sub.package_id for sub in active])

    subscribed_service_ids = {sub.internet_service_id for sub in subscription_rows if sub.internet_service_id}
    for service in service_rows:
        mismatches = []
        if service.organization_id != service.tenant_id:
            mismatches.append("organization")
        if service.customer.tenant_id != service.tenant_id:
            mismatches.append("customer")
        if service.site.tenant_id != service.tenant_id or service.site.customer_id != service.customer_id:
            mismatches.append("site")
        if mismatches:
            add("tenant_mismatch", object_type="InternetService", object_id=service.id, tenant_id=service.tenant_id, mismatches=mismatches)
        if service.id not in subscribed_service_ids:
            add("service_without_subscription", service_id=service.id, customer_id=service.customer_id, site_id=service.site_id)

    for site in site_rows:
        if site.organization_id != site.tenant_id or site.tenant_id != site.customer.tenant_id or site.customer_id != site.customer.id:
            add("tenant_mismatch", object_type="CustomerSite", object_id=site.id, tenant_id=site.tenant_id, organization_id=site.organization_id, customer_id=site.customer_id, customer_tenant_id=site.customer.tenant_id)

    for profile in profile_rows:
        if profile.tenant_id != profile.customer.tenant_id:
            add("tenant_mismatch", object_type="InternetCustomer", object_id=profile.id, tenant_id=profile.tenant_id, customer_id=profile.customer_id, customer_tenant_id=profile.customer.tenant_id)

    for subscription in subscription_rows:
        if not subscription.start_date or (subscription.end_date and subscription.end_date < subscription.start_date):
            add("invalid_subscription_dates", subscription_id=subscription.id, start_date=subscription.start_date, end_date=subscription.end_date)
        if subscription.site_id is None:
            add("subscription_without_site", subscription_id=subscription.id, customer_id=subscription.customer_id)
        if subscription.internet_service_id is None:
            add("subscription_without_service", subscription_id=subscription.id, customer_id=subscription.customer_id, site_id=subscription.site_id)
        mismatches = []
        if subscription.organization_id != subscription.tenant_id:
            mismatches.append("organization")
        if subscription.customer.tenant_id != subscription.tenant_id:
            mismatches.append("customer")
        if subscription.package.tenant_id != subscription.tenant_id:
            mismatches.append("package")
        if subscription.site_id and (subscription.site.tenant_id != subscription.tenant_id or subscription.site.customer_id != subscription.customer_id):
            mismatches.append("site")
        if subscription.internet_service_id and (
            subscription.internet_service.tenant_id != subscription.tenant_id
            or subscription.internet_service.customer_id != subscription.customer_id
            or subscription.internet_service.site_id != subscription.site_id
        ):
            mismatches.append("internet_service")
        if mismatches:
            add("tenant_mismatch", object_type="CustomerSubscription", object_id=subscription.id, tenant_id=subscription.tenant_id, mismatches=mismatches)

    for package in package_rows:
        if package.organization_id != package.tenant_id:
            add("tenant_mismatch", object_type="Package", object_id=package.id, organization_id=package.organization_id, tenant_id=package.tenant_id)

    ip_customers: dict[str, set[int]] = defaultdict(set)
    vlan_customers: dict[str, set[int]] = defaultdict(set)
    for customer in customer_rows:
        if customer.ip_address:
            ip_customers[str(customer.ip_address)].add(customer.id)
        if customer.vlan_id:
            vlan_customers[customer.vlan_id.strip()].add(customer.id)
    for site in site_rows:
        if site.ip_address:
            ip_customers[str(site.ip_address)].add(site.customer_id)
        if site.vlan_id:
            vlan_customers[site.vlan_id.strip()].add(site.customer_id)
    for value, customer_ids in sorted(ip_customers.items()):
        if len(customer_ids) > 1:
            add("duplicate_ip_across_customers", ip_address=value, customer_ids=sorted(customer_ids))
    for value, customer_ids in sorted(vlan_customers.items()):
        if value and len(customer_ids) > 1:
            add("duplicate_vlan_across_customers", vlan_id=value, customer_ids=sorted(customer_ids))

    for document in document_rows:
        if document.organization_id != document.tenant_id or document.customer.tenant_id != document.tenant_id:
            add("financial_document_customer_mismatch", document_id=document.id, document_type=document.document_type, tenant_id=document.tenant_id, customer_id=document.customer_id, customer_tenant_id=document.customer.tenant_id)
        if document.document_type == BillingDocument.DocumentType.RECEIPT:
            if document.invoice_id is None:
                add("receipt_without_invoice", receipt_id=document.id, customer_id=document.customer_id)
            elif document.invoice.tenant_id != document.tenant_id or document.invoice.customer_id != document.customer_id or document.invoice.document_type != BillingDocument.DocumentType.INVOICE:
                add("financial_document_customer_mismatch", document_id=document.id, document_type=document.document_type, customer_id=document.customer_id, invoice_id=document.invoice_id, invoice_customer_id=document.invoice.customer_id, invoice_tenant_id=document.invoice.tenant_id)
        if document.site_id and (document.site.tenant_id != document.tenant_id or document.site.customer_id != document.customer_id):
            add("financial_document_customer_mismatch", document_id=document.id, document_type=document.document_type, customer_id=document.customer_id, site_id=document.site_id)

    for line in line_rows:
        mismatches = []
        if line.organization_id != line.tenant_id or line.document.tenant_id != line.tenant_id:
            mismatches.append("document")
        if line.product_id and line.product.tenant_id != line.tenant_id:
            mismatches.append("product")
        if line.package_id and line.package.tenant_id != line.tenant_id:
            mismatches.append("package")
        if line.internet_service_id and (
            line.internet_service.tenant_id != line.tenant_id
            or line.internet_service.customer_id != line.document.customer_id
            or (line.document.site_id and line.internet_service.site_id != line.document.site_id)
        ):
            mismatches.append("internet_service")
        if line.subscription_id and (
            line.subscription.tenant_id != line.tenant_id
            or line.subscription.customer_id != line.document.customer_id
            or (line.internet_service_id and line.subscription.internet_service_id != line.internet_service_id)
        ):
            mismatches.append("subscription")
        if mismatches:
            add("tenant_mismatch", object_type="BillingLineItem", object_id=line.id, tenant_id=line.tenant_id, mismatches=mismatches)

    for period in period_rows:
        mismatches = []
        subscription = period.subscription
        if period.organization_id != period.tenant_id or subscription.tenant_id != period.tenant_id:
            mismatches.append("subscription")
        if period.invoice_id and (period.invoice.tenant_id != period.tenant_id or period.invoice.customer_id != subscription.customer_id):
            mismatches.append("invoice")
        if period.receipt_id and (period.receipt.tenant_id != period.tenant_id or period.receipt.customer_id != subscription.customer_id or period.receipt.invoice_id != period.invoice_id):
            mismatches.append("receipt")
        if mismatches:
            add("subscription_period_document_mismatch", period_id=period.id, subscription_id=subscription.id, mismatches=mismatches)

    findings = {code: bucket.as_dict() for code, bucket in buckets.items() if bucket.count}
    blocking_count = sum(bucket.count for bucket in buckets.values() if bucket.severity == "blocking")
    review_count = sum(bucket.count for bucket in buckets.values() if bucket.severity == "review")
    return {
        "scope": {"tenant_id": tenant_id, "sample_limit": sample_limit},
        "counts": {
            "customers": len(customer_rows),
            "internet_customers": sum(customer.customer_type == "internet" for customer in customer_rows),
            "walk_in_customers": sum(customer.customer_type == "random" for customer in customer_rows),
            "customer_sites": len(site_rows),
            "internet_services": len(service_rows),
            "internet_profiles": len(profile_rows),
            "packages": len(package_rows),
            "subscriptions": len(subscription_rows),
            "active_subscriptions": sum(sub.status == CustomerSubscription.Status.ACTIVE for sub in subscription_rows),
            "billing_documents": len(document_rows),
            "billing_line_items": len(line_rows),
            "subscription_periods": len(period_rows),
        },
        "summary": {
            "blocking_ambiguities": blocking_count,
            "review_findings": review_count,
            "safe_to_begin_deterministic_backfill": blocking_count == 0,
        },
        "findings": findings,
    }
