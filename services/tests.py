from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from services.models import Package
from users.models import Organization, UserAccessProfile


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
