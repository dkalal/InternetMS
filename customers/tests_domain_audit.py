import json
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from billing.models import BillingDocument, CustomerSubscription
from customers.domain_audit import build_domain_audit
from customers.models import Customer, CustomerSite, InternetCustomer
from services.models import Package
from users.models import Organization


class DomainOwnershipAuditTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Audit Tenant", slug="audit-tenant")
        self.customer = Customer.all_objects.create(
            organization=self.org,
            tenant=self.org,
            name="Audit Internet Customer",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
            ip_address="10.0.0.10",
            vlan_id="100",
        )
        self.site = CustomerSite.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=self.customer,
            name="Main Office",
            location="Moshi",
            ip_address="10.0.0.10",
            vlan_id="100",
            is_primary=True,
        )
        self.package = Package.objects.create(
            organization=self.org,
            tenant=self.org,
            name="Indoor 20",
            package_type="indoor",
            speed="20 Mbps",
            monthly_fee=Decimal("75000.00"),
            setup_fee=Decimal("0.00"),
            description="Audit package",
        )
        self.customer.packages.add(self.package)
        self.site.packages.add(self.package)
        self.profile = InternetCustomer.objects.create(
            customer=self.customer,
            tenant=self.org,
            package_type="indoor",
            start_date=date(2026, 1, 1),
        )
        self.subscription = CustomerSubscription.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=self.customer,
            site=self.site,
            package=self.package,
            start_date=date(2026, 1, 1),
            monthly_fee_at_signup=Decimal("75000.00"),
        )

    def test_consistent_topology_has_no_blocking_findings(self):
        report = build_domain_audit(tenant_id=self.org.id)

        self.assertTrue(report["summary"]["safe_to_begin_deterministic_backfill"])
        self.assertEqual(report["summary"]["blocking_ambiguities"], 0)
        self.assertEqual(report["counts"]["customers"], 1)
        self.assertNotIn("tenant_mismatch", report["findings"])

    def test_audit_detects_date_network_and_multiple_active_service_ambiguity(self):
        self.profile.start_date = date(2025, 12, 1)
        self.profile.package_type = "outdoor"
        self.profile.save(update_fields=["start_date", "package_type"])
        self.site.ip_address = "10.0.0.99"
        self.site.save(update_fields=["ip_address"])
        second_package = Package.objects.create(
            organization=self.org,
            tenant=self.org,
            name="Outdoor 50",
            package_type="outdoor",
            speed="50 Mbps",
            monthly_fee=Decimal("125000.00"),
            setup_fee=Decimal("0.00"),
            description="Second connection or replacement is ambiguous",
        )
        CustomerSubscription.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=self.customer,
            site=self.site,
            package=second_package,
            start_date=date(2026, 2, 1),
            monthly_fee_at_signup=Decimal("125000.00"),
        )

        report = build_domain_audit(tenant_id=self.org.id)

        self.assertFalse(report["summary"]["safe_to_begin_deterministic_backfill"])
        self.assertEqual(report["findings"]["legacy_primary_site_network_conflict"]["count"], 1)
        self.assertEqual(report["findings"]["site_with_multiple_active_subscriptions"]["count"], 1)
        self.assertEqual(report["findings"]["profile_package_type_conflict"]["count"], 1)

    def test_empty_legacy_profile_type_is_review_only_not_a_guessed_conflict(self):
        self.profile.package_type = ""
        self.profile.save(update_fields=["package_type"])

        report = build_domain_audit(tenant_id=self.org.id)

        self.assertTrue(report["summary"]["safe_to_begin_deterministic_backfill"])
        self.assertEqual(report["findings"]["missing_profile_package_type"]["count"], 1)
        self.assertNotIn("profile_package_type_conflict", report["findings"])

    def test_audit_detects_walk_in_topology_without_mutating_rows(self):
        walk_in = Customer.all_objects.create(
            organization=self.org,
            tenant=self.org,
            name="Legacy Walk-in",
            customer_type="random",
            status=Customer.Status.ACTIVE,
            location="Arusha",
        )
        CustomerSite.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=walk_in,
            name="Main Office",
            location="Arusha",
            is_primary=True,
        )
        before = {
            "customers": Customer._base_manager.count(),
            "sites": CustomerSite._base_manager.count(),
            "subscriptions": CustomerSubscription._base_manager.count(),
            "documents": BillingDocument._base_manager.count(),
        }

        report = build_domain_audit(tenant_id=self.org.id)

        after = {
            "customers": Customer._base_manager.count(),
            "sites": CustomerSite._base_manager.count(),
            "subscriptions": CustomerSubscription._base_manager.count(),
            "documents": BillingDocument._base_manager.count(),
        }
        self.assertEqual(before, after)
        self.assertEqual(report["findings"]["walk_in_with_service_topology"]["count"], 1)

    def test_json_management_command_is_machine_readable_and_read_only(self):
        output = StringIO()
        before = CustomerSubscription._base_manager.count()

        call_command(
            "domain_ownership_audit",
            tenant_id=self.org.id,
            format="json",
            stdout=output,
        )

        report = json.loads(output.getvalue())
        self.assertEqual(report["counts"]["subscriptions"], 1)
        self.assertEqual(CustomerSubscription._base_manager.count(), before)
