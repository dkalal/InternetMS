from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import BillingDocument
from billing.services import BillingService, SubscriptionBillingService
from customers.models import Customer
from services.models import Package
from users.models import Organization, TenantMembership, UserAccessProfile


User = get_user_model()


class PackageListViewTests(TestCase):
    def setUp(self):
        self.org1 = Organization.objects.create(name="Tenant A", slug="tenant-a")
        self.org2 = Organization.objects.create(name="Tenant B", slug="tenant-b")
        self.user = User.objects.create_user(username="staff", password="pass")
        UserAccessProfile.objects.create(user=self.user, tenant=self.org1, role=UserAccessProfile.Role.TENANT_STAFF)
        self.client.login(username="staff", password="pass")

    def make_package(self, name, *, org=None, package_type="indoor", monthly_fee="50000.00", active=True):
        return Package.objects.create(
            organization=org or self.org1,
            tenant=org or self.org1,
            name=name,
            package_type=package_type,
            speed="10 Mbps",
            monthly_fee=Decimal(monthly_fee),
            setup_fee=Decimal("0.00"),
            description=f"{name} package",
            is_active=active,
        )

    def test_large_package_list_is_paginated_and_preserves_query(self):
        for index in range(101):
            self.make_package(f"Home {index:03d}")

        response = self.client.get(reverse("package-list"), {"page_size": "50", "search": "Home", "page": "2"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["packages"]), 50)
        self.assertEqual(response.context["result_count"], 101)
        self.assertContains(response, "search=Home")

    def test_package_filters_sort_and_tenant_scope(self):
        wanted = self.make_package("Outdoor Pro", package_type="outdoor")
        self.make_package("Indoor Basic", package_type="indoor")
        self.make_package("Outdoor Other Tenant", org=self.org2, package_type="outdoor")

        response = self.client.get(
            reverse("package-list"),
            {"search": "Outdoor", "type": "outdoor", "subscriber_state": "none", "sort": "bad"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["packages"]), [wanted])
        self.assertEqual(response.context["active_sort"], "name")

    def test_package_create_page_renders(self):
        response = self.client.get(reverse("package-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save package")
        self.assertContains(response, "Price summary")

    def test_package_collected_kpi_includes_partial_subscription_receipts(self):
        UserAccessProfile.objects.filter(user=self.user).update(
            role=UserAccessProfile.Role.TENANT_ADMIN
        )
        TenantMembership.objects.filter(user=self.user, tenant=self.org1).update(
            base_role=TenantMembership.BaseRole.ADMIN_MANAGER
        )
        package = self.make_package("Business Fiber", monthly_fee="100000.00")
        customer = Customer.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Partial Payer",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=customer,
            package=package,
            start_date=date(2026, 8, 1),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.pk,
            period_start=date(2026, 8, 1),
            months=1,
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=period.invoice_id,
            amount_paid=Decimal("50000.00"),
            payment_method="cash",
            payment_reference="package-kpi-partial",
        )

        response = self.client.get(reverse("package-detail", args=[package.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collected_amount"], Decimal("50000.00"))
        period.refresh_from_db()
        period.invoice.refresh_from_db()
        self.assertEqual(period.status, period.Status.INVOICED)
        self.assertEqual(period.invoice.status, BillingDocument.Status.PARTIALLY_PAID)
