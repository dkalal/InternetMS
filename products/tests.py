from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from products.models import Product
from users.models import Organization, UserAccessProfile


User = get_user_model()


class ProductListViewTests(TestCase):
    def setUp(self):
        self.org1 = Organization.objects.create(name="Tenant A", slug="tenant-a")
        self.org2 = Organization.objects.create(name="Tenant B", slug="tenant-b")
        self.user = User.objects.create_user(username="staff", password="pass")
        UserAccessProfile.objects.create(user=self.user, tenant=self.org1, role=UserAccessProfile.Role.TENANT_STAFF)
        self.client.login(username="staff", password="pass")

    def make_product(self, name, *, org=None, category="hardware", quantity=10, active=True):
        return Product.objects.create(
            organization=org or self.org1,
            tenant=org or self.org1,
            name=name,
            category=category,
            quantity=Decimal(str(quantity)),
            measure_unit="Unit",
            buying_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            retail_price=Decimal("25.00"),
            wholesale_price=Decimal("18.00"),
            allow_wholesale=True,
            stock=int(quantity),
            is_active=active,
        )

    def test_large_product_list_is_paginated_and_preserves_query(self):
        for index in range(105):
            self.make_product(f"Router {index:03d}")

        response = self.client.get(reverse("product-list"), {"page_size": "50", "search": "Router", "page": "2"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["products"]), 50)
        self.assertEqual(response.context["result_count"], 105)
        self.assertContains(response, "search=Router")
        self.assertContains(response, "page_size=50")

    def test_product_filters_sort_and_tenant_scope(self):
        wanted = self.make_product("Switch Alpha", category="hardware", quantity=3)
        self.make_product("License Beta", category="software", quantity=20)
        self.make_product("Other Tenant Switch", org=self.org2, category="hardware", quantity=1)

        response = self.client.get(
            reverse("product-list"),
            {"search": "Switch", "category": "hardware", "stock_state": "low", "sort": "not-allowed"},
        )

        self.assertEqual(response.status_code, 200)
        products = list(response.context["products"])
        self.assertEqual(products, [wanted])
        self.assertEqual(response.context["active_sort"], "name")

    def test_product_create_page_renders(self):
        response = self.client.get(reverse("product-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save product")
        self.assertContains(response, "Pricing summary")
