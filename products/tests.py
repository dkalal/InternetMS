from decimal import Decimal
import importlib

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.urls import reverse

from products.forms import ProductForm
from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile


User = get_user_model()


class UnitOfMeasureTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Units Tenant', slug='units-tenant')
        self.other = Organization.objects.create(name='Other Units Tenant', slug='other-units-tenant')
        self.piece = UnitOfMeasure.objects.create(
            organization=self.organization, tenant=self.organization, name='Piece', symbol='Pcs'
        )
        self.box = UnitOfMeasure.objects.create(
            organization=self.organization, tenant=self.organization, name='Box'
        )

    def test_category_default_must_be_an_allowed_tenant_unit(self):
        category = ProductCategory.objects.create(
            organization=self.organization, tenant=self.organization, name='Networking', measure_unit='Pcs'
        )
        category.allowed_units.set([self.piece])
        category.default_unit = self.box
        with self.assertRaises(ValidationError) as error:
            category.full_clean()
        self.assertIn('default_unit', error.exception.message_dict)

        foreign = UnitOfMeasure.objects.create(
            organization=self.other, tenant=self.other, name='Foreign unit'
        )
        category.default_unit = foreign
        with self.assertRaises(ValidationError):
            category.full_clean()

    def test_category_allowed_units_reject_cross_tenant_orm_assignments(self):
        category = ProductCategory.objects.create(
            organization=self.organization, tenant=self.organization, name='Tenant boundary', default_unit=self.piece,
        )
        foreign = UnitOfMeasure.objects.create(
            organization=self.other, tenant=self.other, name='Foreign unit',
        )

        with self.assertRaisesMessage(
            ValidationError, 'Allowed units and product categories must belong to the same tenant.',
        ):
            with transaction.atomic():
                category.allowed_units.add(foreign)

        self.assertFalse(category.allowed_units.filter(pk=foreign.pk).exists())

    def test_product_form_defaults_and_restricts_units_by_category(self):
        category = ProductCategory.objects.create(
            organization=self.organization, tenant=self.organization, name='Devices', default_unit=self.piece
        )
        category.allowed_units.set([self.piece, self.box])
        data = {
            'sku': 'SW-1', 'name': 'Switch', 'item_type': Product.ItemType.PHYSICAL,
            'catalog_category': category.pk, 'sales_unit': self.box.pk, 'buying_price': '100.00',
            'selling_price': '150.00', 'wholesale_min_quantity': '1', 'reorder_threshold': '0',
            'category': 'hardware', 'track_stock': 'on', 'is_active': 'on',
        }
        form = ProductForm(data=data, organization=self.organization)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['sales_unit'], self.box)

        foreign = UnitOfMeasure.objects.create(
            organization=self.other, tenant=self.other, name='Foreign'
        )
        data['sales_unit'] = foreign.pk
        form = ProductForm(data=data, organization=self.organization)
        self.assertFalse(form.is_valid())
        self.assertIn('sales_unit', form.errors)


class TechnicianPriceModelTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Pricing Tenant", slug="pricing-tenant")

    def product(self, **overrides):
        values = {
            "organization": self.organization,
            "tenant": self.organization,
            "name": "Pricing router",
            "quantity": Decimal("0.00"),
            "buying_price": Decimal("100.00"),
            "selling_price": Decimal("150.00"),
        }
        values.update(overrides)
        return Product(**values)

    def test_effective_technician_price_falls_back_to_selling_price(self):
        product = self.product()
        self.assertEqual(product.effective_technician_price, Decimal("150.00"))
        product.technician_price = Decimal("135.00")
        self.assertEqual(product.effective_technician_price, Decimal("135.00"))

    def test_customer_prices_must_be_above_buying_cost(self):
        for field_name in ("selling_price", "wholesale_price", "technician_price"):
            product = self.product(**{field_name: Decimal("100.00")})
            with self.subTest(field=field_name), self.assertRaises(ValidationError) as error:
                product.full_clean()
            self.assertIn(field_name, error.exception.message_dict)

    def test_technician_price_cannot_be_negative(self):
        product = self.product(technician_price=Decimal("-1.00"))
        with self.assertRaises(ValidationError) as error:
            product.full_clean()
        self.assertIn("technician_price", error.exception.message_dict)

    def test_migration_adds_nullable_field_without_copying_legacy_retail_data(self):
        migration = importlib.import_module("products.migrations.0013_product_technician_price")
        add_field = migration.Migration.operations[0]
        self.assertEqual(add_field.name, "technician_price")
        self.assertTrue(add_field.field.null)
        self.assertFalse(any(operation.__class__.__name__ == "RunPython" for operation in migration.Migration.operations))


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
        self.assertNotContains(response, 'name="measure_unit"')
        self.assertNotContains(response, 'name="retail_price"')

    def test_category_unit_is_applied_when_creating_a_catalog_item(self):
        category = ProductCategory.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Cables",
            measure_unit="Meter",
            icon=ProductCategory.Icon.CABLE,
        )
        form = ProductForm(data={
            "sku": "CABLE-001",
            "name": "CAT6 cable",
            "item_type": Product.ItemType.PHYSICAL,
            "catalog_category": category.pk,
            "brand": "",
            "model_number": "",
            "buying_price": "1000.00",
            "selling_price": "1500.00",
            "retail_price": "",
            "wholesale_price": "",
            "wholesale_min_quantity": "1",
            "customer": "",
            "reorder_threshold": "0",
            "category": "hardware",
        }, organization=self.org1)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.instance.measure_unit, "Meter")

    def test_category_unit_is_also_applied_outside_the_html_form(self):
        category = ProductCategory.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Network devices",
            measure_unit="Pc",
            icon=ProductCategory.Icon.ROUTER,
        )
        product = Product.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Edge router",
            sku="EDGE-001",
            catalog_category=category,
            quantity=Decimal("0.00"),
            buying_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
        )

        self.assertEqual(product.measure_unit, "Pc")
