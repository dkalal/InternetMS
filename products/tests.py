from decimal import Decimal
import importlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from audit.models import AuditLog
from products.forms import ProductForm
from products.models import Product, ProductCategory, UnitOfMeasure
from products.services import ProductUnitTransitionService
from inventory.models import InventoryBalance, Purchase, PurchaseLine, StockMovement, Supplier
from users.models import Organization, UserAccessProfile


User = get_user_model()


class ProductDeletionTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='Delete Tenant', slug='delete-tenant')
        self.other = Organization.objects.create(name='Other Delete Tenant', slug='other-delete-tenant')
        self.user = User.objects.create_user(username='delete-manager', password='pass')
        UserAccessProfile.objects.create(
            user=self.user, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.product = Product.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Historical router', sku='DELETE-RTR',
            quantity=Decimal('0'), stock=0, buying_price=Decimal('10'), selling_price=Decimal('20'),
        )
        self.client.login(username='delete-manager', password='pass')

    def test_product_with_purchase_line_is_preserved_and_user_sees_guidance(self):
        supplier = Supplier.objects.create(
            organization=self.tenant, tenant=self.tenant, company_name='Supplier', created_by=self.user,
        )
        purchase = Purchase.objects.create(
            organization=self.tenant, tenant=self.tenant, supplier=supplier,
            reference_number='DELETE-HISTORY', purchase_date='2026-09-21', created_by=self.user,
        )
        line = PurchaseLine.objects.create(
            tenant=self.tenant, purchase=purchase, product=self.product,
            quantity=Decimal('1'), unit_cost=Decimal('10'),
        )
        response = self.client.post(reverse('product-delete', args=[self.product.pk]), follow=True)
        self.assertRedirects(response, reverse('product-detail', args=[self.product.pk]))
        self.assertContains(response, 'cannot be deleted')
        self.assertTrue(Product.objects.filter(pk=self.product.pk, is_active=True).exists())
        self.assertTrue(PurchaseLine.objects.filter(pk=line.pk, product=self.product).exists())

    def test_unreferenced_product_can_still_be_deleted(self):
        response = self.client.post(reverse('product-delete', args=[self.product.pk]))
        self.assertRedirects(response, reverse('product-list'))
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_product_with_positive_legacy_stock_is_not_deleted(self):
        Product.objects.filter(pk=self.product.pk).update(quantity=Decimal('2'), stock=2)
        response = self.client.post(reverse('product-delete', args=[self.product.pk]), follow=True)
        self.assertRedirects(response, reverse('product-detail', args=[self.product.pk]))
        self.assertContains(response, 'still has stock')
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_other_tenant_product_cannot_be_deleted(self):
        foreign = Product.objects.create(
            organization=self.other, tenant=self.other, name='Foreign router', sku='FOREIGN-DELETE',
            quantity=Decimal('0'), stock=0, buying_price=Decimal('10'), selling_price=Decimal('20'),
        )
        response = self.client.post(reverse('product-delete', args=[foreign.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Product.objects.filter(pk=foreign.pk).exists())


def product_image_upload(name='router.jpg', *, size=(2200, 1200), image_format='JPEG'):
    output = BytesIO()
    Image.new('RGB', size, color=(30, 64, 175)).save(output, format=image_format)
    return SimpleUploadedFile(name, output.getvalue(), content_type=f'image/{image_format.lower()}')


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


class ProductStockCostFloorFormTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Cost Floor Tenant', slug='cost-floor-tenant')
        self.other = Organization.objects.create(name='Other Cost Floor Tenant', slug='other-cost-floor-tenant')
        self.manager = User.objects.create_user(username='cost-manager', password='pass')
        UserAccessProfile.objects.create(
            user=self.manager, tenant=self.organization, role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.product = Product.objects.create(
            organization=self.organization, tenant=self.organization, name='Boxed router', sku='BOX-ROUTER',
            buying_price=Decimal('50000'), selling_price=Decimal('80000'),
            wholesale_price=Decimal('75000'), allow_wholesale=True, measure_unit='box',
            quantity=Decimal('15'), stock=15,
        )
        InventoryBalance.objects.create(
            organization=self.organization, tenant=self.organization, product=self.product,
            quantity=Decimal('15'), average_cost=Decimal('70000'),
        )
        StockMovement.objects.create(
            organization=self.organization, tenant=self.organization, product=self.product,
            movement_type=StockMovement.MovementType.PURCHASE_IN,
            quantity=Decimal('15'), balance_after=Decimal('15'), unit_cost=Decimal('70000'),
        )

    def form_data(self, **overrides):
        data = {
            'sku': self.product.sku, 'name': self.product.name,
            'item_type': Product.ItemType.PHYSICAL, 'sales_unit': self.product.sales_unit_id,
            'buying_price': '50000', 'selling_price': '70000',
            'wholesale_price': '60000', 'wholesale_min_quantity': '5',
            'allow_wholesale': 'on', 'track_stock': 'on', 'is_active': 'on',
            'category': 'hardware', 'reorder_threshold': '0',
        }
        data.update(overrides)
        return data

    def test_manager_sees_actual_floor_and_both_unsafe_prices_are_rejected(self):
        self.client.login(username='cost-manager', password='pass')
        url = reverse('product-update', args=[self.product.pk])
        response = self.client.get(url)
        self.assertContains(response, 'weighted-average cost of stock on hand')
        self.assertContains(response, '70,000.000000')

        response = self.client.post(url, self.form_data())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'current inventory cost of TZS 70,000.000000', count=2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.selling_price, Decimal('80000'))
        self.assertEqual(self.product.wholesale_price, Decimal('75000'))

    def test_cost_detail_is_not_disclosed_without_permission(self):
        form = ProductForm(
            data=self.form_data(), instance=self.product, organization=self.organization,
        )
        self.assertFalse(form.is_valid())
        self.assertIsNone(form.current_cost_floor)
        self.assertIn('cost per selected sales unit', str(form.errors['selling_price']))
        self.assertNotIn('70,000', str(form.errors))

    def test_without_movement_backed_stock_submitted_buying_price_is_floor(self):
        product = Product.objects.create(
            organization=self.organization, tenant=self.organization, name='Service plan', sku='SERVICE-PLAN',
            item_type=Product.ItemType.SERVICE, track_stock=False,
            buying_price=Decimal('50000'), selling_price=Decimal('80000'), quantity=Decimal('0'), stock=0,
        )
        data = self.form_data(
            sku=product.sku, name=product.name, item_type=Product.ItemType.SERVICE,
            buying_price='40000', selling_price='45000', wholesale_price='',
        )
        form = ProductForm(data=data, instance=product, organization=self.organization, actor=self.manager)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.current_cost_floor, Decimal('40000'))

    def test_other_tenant_stock_cost_cannot_set_product_floor(self):
        foreign = Product.objects.create(
            organization=self.other, tenant=self.other, name='Foreign router', sku='FOREIGN-ROUTER',
            buying_price=Decimal('100'), selling_price=Decimal('150'), quantity=Decimal('1'), stock=1,
        )
        InventoryBalance.objects.create(
            organization=self.other, tenant=self.other, product=foreign,
            quantity=Decimal('1'), average_cost=Decimal('999999'),
        )
        StockMovement.objects.create(
            organization=self.other, tenant=self.other, product=foreign,
            movement_type=StockMovement.MovementType.PURCHASE_IN,
            quantity=Decimal('1'), balance_after=Decimal('1'), unit_cost=Decimal('999999'),
        )
        foreign_form = ProductForm(instance=foreign, organization=self.organization, actor=self.manager)
        self.assertIsNone(foreign_form.current_cost_floor)
        form = ProductForm(
            data=self.form_data(selling_price='70000.01', wholesale_price='70000.01'),
            instance=self.product, organization=self.organization, actor=self.manager,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_sales_unit_is_locked_after_inventory_history_exists(self):
        other_unit = UnitOfMeasure.objects.create(
            organization=self.organization, tenant=self.organization, name='Piece',
        )
        form = ProductForm(instance=self.product, organization=self.organization, actor=self.manager)

        self.assertTrue(form.fields['sales_unit'].disabled)

        self.product.sales_unit = other_unit
        self.product.measure_unit = other_unit.label
        with self.assertRaisesMessage(
            ValidationError, 'Sales/stock unit cannot be changed after transaction history exists.',
        ):
            self.product.save()

        self.product.refresh_from_db()
        self.assertNotEqual(self.product.sales_unit_id, other_unit.pk)


class ProductUnitSuccessorTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Unit Transition Tenant', slug='unit-transition')
        self.other = Organization.objects.create(name='Other Unit Tenant', slug='other-unit-transition')
        self.manager = User.objects.create_user(username='unit-manager', password='pass')
        UserAccessProfile.objects.create(
            user=self.manager, tenant=self.organization, role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.sales_user = User.objects.create_user(username='unit-sales', password='pass')
        UserAccessProfile.objects.create(
            user=self.sales_user, tenant=self.organization, role=UserAccessProfile.Role.TENANT_STAFF,
        )
        self.meter = UnitOfMeasure.objects.create(
            organization=self.organization, tenant=self.organization, name='Meter', symbol='m',
        )
        self.box = UnitOfMeasure.objects.create(
            organization=self.organization, tenant=self.organization, name='Box',
        )
        self.category = ProductCategory.objects.create(
            organization=self.organization, tenant=self.organization, name='Cables', default_unit=self.meter,
        )
        self.category.allowed_units.set([self.meter, self.box])
        self.source = Product.objects.create(
            organization=self.organization, tenant=self.organization, name='CAT6 Cable', sku='CAT6-M',
            catalog_category=self.category, sales_unit=self.meter, measure_unit=self.meter.label,
            buying_price=Decimal('700'), selling_price=Decimal('2000'),
            quantity=Decimal('10'), stock=10, track_stock=True,
        )
        InventoryBalance.objects.create(
            organization=self.organization, tenant=self.organization, product=self.source,
            quantity=Decimal('10'), average_cost=Decimal('700'),
        )
        self.movement = StockMovement.objects.create(
            organization=self.organization, tenant=self.organization, product=self.source,
            movement_type=StockMovement.MovementType.OPENING, quantity=Decimal('10'),
            balance_after=Decimal('10'), unit_cost=Decimal('700'), created_by=self.manager,
        )

    def values(self):
        return {
            'name': 'CAT6 Cable Box', 'sku': 'CAT6-BOX',
            'catalog_category': self.category, 'sales_unit': self.box,
            'buying_price': Decimal('200000'), 'selling_price': Decimal('250000'),
            'technician_price': None, 'allow_wholesale': False,
            'wholesale_price': None, 'wholesale_min_quantity': Decimal('1'),
            'reason': 'Future CAT6 purchasing and sales are by unopened box.',
        }

    def test_service_creates_zero_stock_successor_without_reassigning_history(self):
        transition_key = uuid4()
        successor, created = ProductUnitTransitionService.create_successor(
            organization=self.organization, source_product_id=self.source.pk,
            actor=self.manager, transition_key=transition_key, values=self.values(),
        )

        self.assertTrue(created)
        self.assertEqual(successor.sales_unit, self.box)
        self.assertEqual(successor.quantity, Decimal('0'))
        self.assertEqual(successor.stock, 0)
        self.source.refresh_from_db()
        self.assertFalse(self.source.is_active)
        self.movement.refresh_from_db()
        self.assertEqual(self.movement.product_id, self.source.pk)
        self.assertFalse(InventoryBalance.objects.filter(product=successor).exists())
        event = AuditLog.objects.get(action='inventory.product.unit_successor_created')
        self.assertEqual(event.metadata['successor_product_id'], successor.pk)
        self.assertFalse(event.metadata['stock_transferred'])

        repeated, repeated_created = ProductUnitTransitionService.create_successor(
            organization=self.organization, source_product_id=self.source.pk,
            actor=self.manager, transition_key=transition_key, values=self.values(),
        )
        self.assertFalse(repeated_created)
        self.assertEqual(repeated.pk, successor.pk)
        self.assertEqual(Product.objects.filter(tenant=self.organization, sku='CAT6-BOX').count(), 1)

    def test_service_rejects_cross_tenant_source(self):
        with self.assertRaises(PermissionDenied):
            ProductUnitTransitionService.create_successor(
                organization=self.other, source_product_id=self.source.pk,
                actor=self.manager, transition_key=uuid4(), values=self.values(),
            )

    def test_service_rechecks_permission_outside_the_view(self):
        with self.assertRaises(PermissionDenied):
            ProductUnitTransitionService.create_successor(
                organization=self.organization, source_product_id=self.source.pk,
                actor=self.sales_user, transition_key=uuid4(), values=self.values(),
            )
        self.assertTrue(Product.objects.get(pk=self.source.pk).is_active)
        self.assertFalse(Product.objects.filter(tenant=self.organization, sku='CAT6-BOX').exists())

    def test_manager_can_use_guided_successor_page(self):
        self.client.login(username='unit-manager', password='pass')
        url = reverse('product-unit-successor', args=[self.source.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No automatic conversion')
        self.assertContains(response, '10 m')

        data = {
            'name': 'CAT6 Cable Box', 'sku': 'CAT6-BOX',
            'catalog_category': self.category.pk, 'sales_unit': self.box.pk,
            'buying_price': '200000', 'selling_price': '250000',
            'technician_price': '', 'allow_wholesale': '', 'wholesale_price': '',
            'wholesale_min_quantity': '1',
            'reason': 'Future CAT6 purchasing and sales are by unopened box.',
            'acknowledge': 'on', 'transition_key': response.context['form']['transition_key'].value(),
        }
        response = self.client.post(url, data)
        successor = Product.objects.get(tenant=self.organization, sku='CAT6-BOX')
        self.assertRedirects(response, reverse('product-detail', args=[successor.pk]))

    def test_sales_role_and_cross_tenant_object_are_denied(self):
        self.client.login(username='unit-sales', password='pass')
        response = self.client.get(reverse('product-unit-successor', args=[self.source.pk]))
        self.assertEqual(response.status_code, 403)

        self.client.login(username='unit-manager', password='pass')
        foreign_unit = UnitOfMeasure.objects.create(
            organization=self.other, tenant=self.other, name='Foreign unit',
        )
        foreign = Product.objects.create(
            organization=self.other, tenant=self.other, name='Foreign product', sku='FOREIGN-UNIT',
            sales_unit=foreign_unit, measure_unit=foreign_unit.label,
            buying_price=Decimal('10'), selling_price=Decimal('20'), quantity=Decimal('1'), stock=1,
        )
        response = self.client.get(reverse('product-unit-successor', args=[foreign.pk]))
        self.assertEqual(response.status_code, 404)


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
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'data-product-image-preview')
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


class ProductImageTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.organization = Organization.objects.create(name='Image Tenant', slug='image-tenant')
        self.unit = UnitOfMeasure.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Piece',
            symbol='Pcs',
        )
        self.category = ProductCategory.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Routers',
            default_unit=self.unit,
        )
        self.category.allowed_units.set([self.unit])

    def tearDown(self):
        self.media_override.disable()
        self.media_directory.cleanup()

    def form_data(self):
        return {
            'sku': 'RTR-IMAGE-1',
            'name': 'Catalog router',
            'item_type': Product.ItemType.PHYSICAL,
            'catalog_category': self.category.pk,
            'sales_unit': self.unit.pk,
            'brand': '',
            'model_number': '',
            'buying_price': '100.00',
            'selling_price': '150.00',
            'technician_price': '',
            'wholesale_price': '',
            'wholesale_min_quantity': '1',
            'track_stock': 'on',
            'tax_eligible': 'on',
            'reorder_threshold': '0',
            'is_active': 'on',
            'description': '',
            'category': 'hardware',
        }

    def test_product_form_optimizes_photo_and_stores_it_under_tenant_path(self):
        form = ProductForm(
            data=self.form_data(),
            files={'image': product_image_upload()},
            organization=self.organization,
        )

        self.assertTrue(form.is_valid(), form.errors)
        product = form.save(commit=False)
        product.organization = self.organization
        product.tenant = self.organization
        product.quantity = Decimal('0.00')
        product.stock = 0
        product.save()

        self.assertTrue(product.image.name.startswith(f'product_images/tenant_{self.organization.pk}/'))
        self.assertEqual(Path(product.image.name).suffix, '.webp')
        with Image.open(product.image.path) as saved_image:
            self.assertLessEqual(max(saved_image.size), 1600)
            self.assertEqual(saved_image.format, 'WEBP')

    def test_product_form_rejects_a_fake_image(self):
        form = ProductForm(
            data=self.form_data(),
            files={'image': SimpleUploadedFile('fake.jpg', b'not an image', content_type='image/jpeg')},
            organization=self.organization,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    def test_replaced_product_photo_is_removed_after_commit(self):
        product = Product.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Replace image router',
            sku='RTR-REPLACE',
            catalog_category=self.category,
            sales_unit=self.unit,
            image=product_image_upload(size=(200, 200)),
            quantity=Decimal('0.00'),
            buying_price=Decimal('100.00'),
            selling_price=Decimal('150.00'),
        )
        previous_path = product.image.path

        with self.captureOnCommitCallbacks(execute=True):
            product.image = product_image_upload('replacement.png', size=(300, 300), image_format='PNG')
            product.save()

        self.assertFalse(Path(previous_path).exists())
        self.assertTrue(Path(product.image.path).exists())
