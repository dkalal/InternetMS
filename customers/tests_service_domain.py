import importlib
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from billing.models import BillingDocument, CustomerSubscription, SubscriptionPeriod
from billing.services import BillingService, LineItemInput, SubscriptionBillingService
from customers.models import Customer, CustomerSite, InternetCustomer, InternetService
from customers.services import InternetServiceDomainService
from services.models import Package
from users.models import Organization, TenantMembership


def make_package(org, name, package_type="indoor", price="75000.00"):
    return Package.objects.create(
        organization=org,
        tenant=org,
        name=name,
        package_type=package_type,
        speed="20 Mbps",
        monthly_fee=Decimal(price),
        setup_fee=Decimal("0.00"),
        description=f"{name} package",
    )


class InternetServiceDomainTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Service Tenant", slug="service-tenant")
        self.other_org = Organization.objects.create(name="Other Tenant", slug="other-tenant")
        self.customer = Customer.all_objects.create(
            organization=self.org,
            tenant=self.org,
            name="Multi Office Customer",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        self.primary = CustomerSite.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=self.customer,
            name="Head Office",
            location="Moshi",
            is_primary=True,
        )
        self.branch = CustomerSite.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=self.customer,
            name="Branch",
            location="Arusha",
        )

    def test_customer_can_have_multiple_sites_and_site_multiple_services(self):
        first = InternetService.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=self.customer,
            site=self.primary,
            service_code="SVC-001",
            name="Primary fibre",
        )
        second = InternetService.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=self.customer,
            site=self.primary,
            service_code="SVC-002",
            name="Backup wireless",
        )

        self.assertEqual(self.customer.sites.count(), 2)
        self.assertEqual(list(self.primary.internet_services.order_by("id")), [first, second])

    def test_retired_pricing_tiers_are_not_available(self):
        values = {value for value, _label in Customer.PricingTier.choices}
        self.assertEqual(values, {"retail", "technician", "wholesale"})

        with self.assertRaises(IntegrityError), transaction.atomic():
            Customer.all_objects.create(
                organization=self.org,
                tenant=self.org,
                name="Retired Tier",
                customer_type="random",
                status=Customer.Status.ACTIVE,
                pricing_tier="corporate",
                location="Moshi",
            )

    def test_one_customer_can_use_indoor_and_outdoor_packages_on_different_services(self):
        indoor = make_package(self.org, "Indoor", "indoor")
        outdoor = make_package(self.org, "Outdoor", "outdoor", "125000.00")
        first = InternetService.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer,
            site=self.primary, service_code="SVC-IN", name="Indoor service",
        )
        second = InternetService.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer,
            site=self.branch, service_code="SVC-OUT", name="Outdoor service",
        )
        CustomerSubscription.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer,
            site=self.primary, internet_service=first, package=indoor,
            start_date=date(2026, 1, 1), monthly_fee_at_signup=indoor.monthly_fee,
        )
        CustomerSubscription.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer,
            site=self.branch, internet_service=second, package=outdoor,
            start_date=date(2026, 1, 1), monthly_fee_at_signup=outdoor.monthly_fee,
        )

        self.assertEqual(
            set(self.customer.subscriptions.values_list("package__package_type", flat=True)),
            {"indoor", "outdoor"},
        )

    def test_database_allows_only_one_active_subscription_per_service(self):
        first_package = make_package(self.org, "First")
        second_package = make_package(self.org, "Second")
        service = InternetService.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer,
            site=self.primary, service_code="SVC-ONE", name="One service",
        )
        CustomerSubscription.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer,
            site=self.primary, internet_service=service, package=first_package,
            start_date=date(2026, 1, 1), monthly_fee_at_signup=first_package.monthly_fee,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerSubscription.objects.create(
                organization=self.org, tenant=self.org, customer=self.customer,
                site=self.primary, internet_service=service, package=second_package,
                start_date=date(2026, 2, 1), monthly_fee_at_signup=second_package.monthly_fee,
            )

    def test_subscription_end_date_cannot_precede_start_date(self):
        package = make_package(self.org, "Date Package")
        service = InternetService.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer,
            site=self.primary, service_code="SVC-DATE", name="Date service",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerSubscription.objects.create(
                organization=self.org, tenant=self.org, customer=self.customer,
                site=self.primary, internet_service=service, package=package,
                start_date=date(2026, 2, 1), end_date=date(2026, 1, 31),
                monthly_fee_at_signup=package.monthly_fee,
            )

    def test_service_rejects_cross_tenant_and_wrong_customer_site(self):
        other_customer = Customer.all_objects.create(
            organization=self.other_org, tenant=self.other_org, name="Other Customer",
            customer_type="internet", status=Customer.Status.ACTIVE, location="Dar",
        )
        with self.assertRaises(ValueError):
            InternetService.objects.create(
                organization=self.org, tenant=self.org, customer=other_customer,
                site=self.primary, service_code="BAD-TENANT", name="Invalid",
            )

        same_tenant_other_customer = Customer.all_objects.create(
            organization=self.org, tenant=self.org, name="Same Tenant Other",
            customer_type="internet", status=Customer.Status.ACTIVE, location="Dar",
        )
        with self.assertRaises(ValueError):
            InternetService.objects.create(
                organization=self.org, tenant=self.org, customer=same_tenant_other_customer,
                site=self.primary, service_code="BAD-CUSTOMER", name="Invalid",
            )

    def test_financial_context_is_optional_and_validated(self):
        invoice = BillingDocument.objects.create(
            organization=self.org,
            tenant=self.org,
            document_type=BillingDocument.DocumentType.INVOICE,
            number="INV-CONTEXT-001",
            customer=self.customer,
            issue_date=date(2026, 1, 1),
        )
        invoice.site = self.branch
        invoice.save()
        self.assertEqual(invoice.site, self.branch)

        other_customer = Customer.all_objects.create(
            organization=self.org, tenant=self.org, name="Wrong Customer",
            customer_type="random", status=Customer.Status.ACTIVE, location="Dar",
        )
        wrong_site = CustomerSite.objects.create(
            organization=self.org, tenant=self.org, customer=other_customer,
            name="Wrong Site", location="Dar", is_primary=True,
        )
        invoice.site = wrong_site
        with self.assertRaises(ValidationError):
            invoice.save()


class InternetServiceCommandTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Command Tenant", slug="command-tenant")
        self.user = get_user_model().objects.create_user(username="domain-admin", password="test")
        TenantMembership.objects.create(
            tenant=self.org, user=self.user,
            base_role=TenantMembership.BaseRole.ADMIN_MANAGER,
        )
        self.customer = Customer.all_objects.create(
            organization=self.org, tenant=self.org, name="Command Customer",
            customer_type="internet", status=Customer.Status.ACTIVE, location="Moshi",
        )
        self.site = CustomerSite.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer,
            name="Head Office", location="Moshi", is_primary=True,
        )
        self.old_package = make_package(self.org, "Legacy 20")
        self.new_package = make_package(self.org, "Upgrade 50", price="150000.00")
        self.service = InternetServiceDomainService.add_internet_service(
            organization=self.org, actor=self.user, customer_id=self.customer.id,
            site_id=self.site.id, service_code="SVC-CMD-001", name="Main fibre",
        )
        self.subscription = InternetServiceDomainService.assign_initial_subscription(
            organization=self.org, actor=self.user, service_id=self.service.id,
            package_id=self.old_package.id, start_date=date(2026, 1, 1),
        )

    def test_package_change_closes_old_history_and_creates_new_agreement(self):
        period = SubscriptionBillingService.create_period(
            organization=self.org, subscription=self.subscription,
            period_start=date(2026, 7, 1),
        )
        old_id = self.subscription.id
        old_price = self.subscription.monthly_fee_at_signup

        replacement = InternetServiceDomainService.change_service_package(
            organization=self.org, actor=self.user, service_id=self.service.id,
            package_id=self.new_package.id, effective_date=date(2026, 8, 1),
            reason="Customer approved capacity upgrade",
        )

        self.subscription.refresh_from_db()
        period.refresh_from_db()
        self.assertEqual(self.subscription.id, old_id)
        self.assertEqual(self.subscription.package_id, self.old_package.id)
        self.assertEqual(self.subscription.monthly_fee_at_signup, old_price)
        self.assertEqual(self.subscription.status, CustomerSubscription.Status.CANCELLED)
        self.assertEqual(self.subscription.end_date, date(2026, 7, 31))
        self.assertEqual(period.subscription_id, old_id)
        self.assertEqual(replacement.package_id, self.new_package.id)
        self.assertEqual(replacement.monthly_fee_at_signup, self.new_package.monthly_fee)
        self.assertEqual(replacement.start_date, date(2026, 8, 1))

    def test_block_and_disconnect_do_not_cancel_commercial_agreement(self):
        InternetServiceDomainService.block_service(
            organization=self.org, actor=self.user, service_id=self.service.id,
            reason="Temporary network policy block",
        )
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, CustomerSubscription.Status.ACTIVE)

        InternetServiceDomainService.disconnect_service(
            organization=self.org, actor=self.user, service_id=self.service.id,
            reason="Physical service removed",
        )
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, CustomerSubscription.Status.ACTIVE)

    def test_actor_without_customer_update_permission_is_rejected(self):
        technician = get_user_model().objects.create_user(username="domain-tech", password="test")
        TenantMembership.objects.create(
            tenant=self.org, user=technician,
            base_role=TenantMembership.BaseRole.TECHNICIAN,
        )
        with self.assertRaises(PermissionDenied):
            InternetServiceDomainService.block_service(
                organization=self.org, actor=technician, service_id=self.service.id,
                reason="Not authorized",
            )

    def test_subscription_invoice_carries_site_service_and_subscription_context(self):
        period = SubscriptionBillingService.create_period(
            organization=self.org, subscription=self.subscription,
            period_start=date(2026, 8, 1),
        )
        invoice = SubscriptionBillingService.create_invoice_for_period(
            organization=self.org, created_by=self.user, period=period,
        )
        line = invoice.items.get()
        self.assertEqual(invoice.site_id, self.site.id)
        self.assertEqual(line.internet_service_id, self.service.id)
        self.assertEqual(line.subscription_id, self.subscription.id)

    def test_same_package_can_be_active_on_two_distinct_services_at_one_site(self):
        backup = InternetServiceDomainService.add_internet_service(
            organization=self.org, actor=self.user, customer_id=self.customer.id,
            site_id=self.site.id, service_code="SVC-CMD-002", name="Backup fibre",
        )
        second = InternetServiceDomainService.assign_initial_subscription(
            organization=self.org, actor=self.user, service_id=backup.id,
            package_id=self.old_package.id, start_date=date(2026, 2, 1),
        )
        self.assertNotEqual(second.internet_service_id, self.subscription.internet_service_id)

    def test_customer_and_package_pages_render_service_context(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.org.id
        session.save()

        customer_response = self.client.get(reverse("customer-detail", args=[self.customer.id]))
        package_response = self.client.get(reverse("package-detail", args=[self.old_package.id]))

        self.assertEqual(customer_response.status_code, 200)
        self.assertContains(customer_response, "Sites &amp; Internet services")
        self.assertContains(customer_response, self.service.service_code)
        self.assertContains(customer_response, "Operational services")
        self.assertEqual(package_response.status_code, 200)
        self.assertContains(package_response, "Service subscriptions")
        self.assertContains(package_response, self.service.service_code)

    def test_add_service_page_rejects_cross_tenant_site(self):
        other_org = Organization.objects.create(name="Command Other", slug="command-other")
        other_customer = Customer.all_objects.create(
            organization=other_org, tenant=other_org, name="Other Account",
            customer_type="internet", status=Customer.Status.ACTIVE, location="Arusha",
        )
        other_site = CustomerSite.objects.create(
            organization=other_org, tenant=other_org, customer=other_customer,
            name="Other Site", location="Arusha", is_primary=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.org.id
        session.save()

        response = self.client.post(
            reverse("internet-service-create", args=[self.customer.id]),
            {
                "site": other_site.id,
                "service_code": "CROSS-TENANT",
                "name": "Invalid service",
                "package": "",
                "subscription_start_date": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(InternetService.objects.filter(service_code="CROSS-TENANT").exists())

    def test_customer_identity_edit_does_not_replace_service_subscription(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.org.id
        session.save()
        before = {
            "subscription_id": self.subscription.id,
            "package_id": self.subscription.package_id,
            "start_date": self.subscription.start_date,
            "price": self.subscription.monthly_fee_at_signup,
        }

        response = self.client.post(
            reverse("customer-update", args=[self.customer.id]),
            {
                "name": "Renamed Account",
                "customer_type": "internet",
                "status": Customer.Status.ACTIVE,
                "pricing_tier": Customer.PricingTier.RETAIL,
                "location": "Moshi",
                "email": "renamed@example.com",
                "status_change_reason": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.subscription.refresh_from_db()
        self.assertEqual(
            {
                "subscription_id": self.subscription.id,
                "package_id": self.subscription.package_id,
                "start_date": self.subscription.start_date,
                "price": self.subscription.monthly_fee_at_signup,
            },
            before,
        )

    def test_customer_edit_summary_uses_current_subscription_agreement(self):
        period = SubscriptionBillingService.create_period(
            organization=self.org,
            subscription=self.subscription,
            period_start=date(2026, 1, 1),
            months=3,
        )
        SubscriptionPeriod.objects.filter(pk=period.pk).update(status=SubscriptionPeriod.Status.PAID)
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.org.id
        session.save()

        response = self.client.get(reverse("customer-update", args=[self.customer.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current agreement")
        self.assertContains(response, "01 Jan 2026")
        self.assertContains(response, "No scheduled end")
        self.assertContains(response, self.old_package.name)
        self.assertContains(response, "Paid service coverage")
        self.assertContains(response, "01 Jan 2026 – 31 Mar 2026")
        self.assertNotContains(response, "Not set</span><span aria-hidden=\"true\"> – </span>")

    def test_document_and_receipt_inherit_optional_site_context(self):
        invoice = BillingService.create_document(
            organization=self.org,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.id,
            site_id=self.site.id,
            status=BillingDocument.Status.ISSUED,
            items=[LineItemInput(description="Site survey", unit_price=Decimal("25000.00"))],
        )
        receipt = BillingService.create_document(
            organization=self.org,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.RECEIPT,
            customer_id=self.customer.id,
            invoice_id=invoice.id,
            status=BillingDocument.Status.PAID,
            items=[LineItemInput(description="Payment", unit_price=Decimal("25000.00"))],
        )
        self.assertEqual(invoice.site_id, self.site.id)
        self.assertEqual(receipt.site_id, self.site.id)

    def test_package_with_history_cannot_be_deleted_from_ui(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.org.id
        session.save()

        response = self.client.post(reverse("package-delete", args=[self.old_package.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "protected subscription or financial history")
        self.assertTrue(Package.objects.filter(pk=self.old_package.id).exists())


class InternetServiceBackfillTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Backfill Tenant", slug="backfill-tenant")
        self.package = make_package(self.org, "Legacy Package")

    def run_backfill(self):
        migration = importlib.import_module("billing.migrations.0029_backfill_internet_services")
        migration.backfill_internet_services(
            django_apps,
            SimpleNamespace(connection=connection),
        )

    def test_backfill_preserves_subscription_identity_and_is_idempotent(self):
        customer = Customer.all_objects.create(
            organization=self.org, tenant=self.org, name="Legacy Internet",
            customer_type="internet", status=Customer.Status.ACTIVE,
            location="Moshi", ip_address="10.1.1.5", vlan_id="200",
        )
        site = CustomerSite.objects.create(
            organization=self.org, tenant=self.org, customer=customer,
            name="Main Office", location="Moshi", is_primary=True,
        )
        InternetCustomer.objects.create(
            customer=customer, tenant=self.org, package_type="",
            start_date=date(2025, 12, 1),
        )
        subscription = CustomerSubscription.objects.create(
            organization=self.org, tenant=self.org, customer=customer, site=site,
            package=self.package, status=CustomerSubscription.Status.ACTIVE,
            start_date=date(2026, 1, 1), end_date=None,
            monthly_fee_at_signup=Decimal("70000.00"),
        )
        original = {
            "id": subscription.id,
            "package_id": subscription.package_id,
            "start_date": subscription.start_date,
            "end_date": subscription.end_date,
            "status": subscription.status,
            "price": subscription.monthly_fee_at_signup,
        }

        self.run_backfill()
        self.run_backfill()

        subscription.refresh_from_db()
        self.assertEqual(InternetService._base_manager.count(), 1)
        self.assertIsNotNone(subscription.internet_service_id)
        self.assertEqual(subscription.internet_service.site_id, site.id)
        self.assertEqual(subscription.internet_service.ip_address, "10.1.1.5")
        self.assertEqual(subscription.internet_service.operational_status, "unknown")
        self.assertEqual(
            {
                "id": subscription.id,
                "package_id": subscription.package_id,
                "start_date": subscription.start_date,
                "end_date": subscription.end_date,
                "status": subscription.status,
                "price": subscription.monthly_fee_at_signup,
            },
            original,
        )

    def test_backfill_creates_one_service_per_subscription_site(self):
        customer = Customer.all_objects.create(
            organization=self.org, tenant=self.org, name="Two Office Internet",
            customer_type="internet", status=Customer.Status.ACTIVE, location="Moshi",
        )
        sites = [
            CustomerSite.objects.create(
                organization=self.org, tenant=self.org, customer=customer,
                name="Head", location="Moshi", is_primary=True,
            ),
            CustomerSite.objects.create(
                organization=self.org, tenant=self.org, customer=customer,
                name="Branch", location="Arusha",
            ),
        ]
        subscriptions = []
        for index, site in enumerate(sites):
            subscriptions.append(CustomerSubscription.objects.create(
                organization=self.org, tenant=self.org, customer=customer, site=site,
                package=self.package, status=CustomerSubscription.Status.ACTIVE,
                start_date=date(2026, index + 1, 1),
                monthly_fee_at_signup=self.package.monthly_fee,
            ))

        self.run_backfill()

        self.assertEqual(InternetService._base_manager.filter(customer=customer).count(), 2)
        for subscription in subscriptions:
            subscription.refresh_from_db()
            self.assertEqual(subscription.internet_service.site_id, subscription.site_id)

    def test_backfill_does_not_create_walk_in_service_or_subscription(self):
        walk_in = Customer.all_objects.create(
            organization=self.org, tenant=self.org, name="Walk-in",
            customer_type="random", status=Customer.Status.ACTIVE, location="Arusha",
        )
        CustomerSite.objects.create(
            organization=self.org, tenant=self.org, customer=walk_in,
            name="Generated Main Office", location="Arusha", is_primary=True,
        )

        self.run_backfill()

        self.assertFalse(InternetService._base_manager.filter(customer=walk_in).exists())
        self.assertFalse(CustomerSubscription._base_manager.filter(customer=walk_in).exists())

    def test_no_subscription_customer_requires_profile_or_network_proof(self):
        proven = Customer.all_objects.create(
            organization=self.org, tenant=self.org, name="Profile Proven",
            customer_type="internet", status=Customer.Status.ACTIVE, location="Moshi",
        )
        unproven = Customer.all_objects.create(
            organization=self.org, tenant=self.org, name="Unproven",
            customer_type="internet", status=Customer.Status.ACTIVE, location="Moshi",
        )
        for customer in (proven, unproven):
            CustomerSite.objects.create(
                organization=self.org, tenant=self.org, customer=customer,
                name="Main Office", location="Moshi", is_primary=True,
            )
        InternetCustomer.objects.create(customer=proven, tenant=self.org, package_type="")

        self.run_backfill()

        self.assertTrue(InternetService._base_manager.filter(customer=proven).exists())
        self.assertFalse(InternetService._base_manager.filter(customer=unproven).exists())
