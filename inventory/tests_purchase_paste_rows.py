from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from .models import Purchase, StockMovement


User = get_user_model()


class PurchasePasteRowsTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='Paste Tenant', slug='paste-tenant')
        self.other = Organization.objects.create(name='Other Paste Tenant', slug='other-paste-tenant')
        self.admin = User.objects.create_user(username='paste-admin', password='pass')
        UserAccessProfile.objects.create(user=self.admin, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)
        unit = UnitOfMeasure.objects.create(organization=self.tenant, tenant=self.tenant, name='Piece', symbol='pc')
        category = ProductCategory.objects.create(organization=self.tenant, tenant=self.tenant, name='Devices', default_unit=unit)
        self.product = Product.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Router', sku='PASTE-RTR',
            catalog_category=category, sales_unit=unit, quantity=Decimal('0'), stock=0,
            buying_price=Decimal('50'), selling_price=Decimal('100'), track_stock=True,
        )
        self.serialized = Product.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Radio', sku='PASTE-RAD',
            catalog_category=category, sales_unit=unit, quantity=Decimal('0'), stock=0,
            buying_price=Decimal('50'), selling_price=Decimal('100'), track_stock=True, is_serialized=True,
        )
        other_unit = UnitOfMeasure.objects.create(organization=self.other, tenant=self.other, name='Piece', symbol='pc')
        other_category = ProductCategory.objects.create(organization=self.other, tenant=self.other, name='Devices', default_unit=other_unit)
        Product.objects.create(
            organization=self.other, tenant=self.other, name='Foreign', sku='FOREIGN-SKU',
            catalog_category=other_category, sales_unit=other_unit, quantity=Decimal('0'), stock=0,
            buying_price=Decimal('50'), selling_price=Decimal('100'), track_stock=True,
        )
        self.url = reverse('inventory:purchase_rows_preview')

    def test_authentication_post_and_csrf_are_required(self):
        self.assertEqual(self.client.post(self.url, {'rows': 'PASTE-RTR\t2\t50'}).status_code, 302)
        self.client.login(username='paste-admin', password='pass')
        self.assertEqual(self.client.get(self.url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username='paste-admin', password='pass')
        self.assertEqual(csrf_client.post(self.url, {'rows': 'PASTE-RTR\t2\t50'}).status_code, 403)

    def test_valid_rows_and_optional_header_are_normalized(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.client.post(self.url, {'rows': 'SKU\tQuantity\tUnit cost\tBatch\tExpiry\tSerials\nPASTE-RTR\t2.5\t75.25\tB-1\t2027-01-02\t'})
        self.assertEqual(response.status_code, 200)
        row = response.json()['rows'][0]
        self.assertTrue(row['valid'])
        self.assertEqual(row['product']['id'], self.product.pk)
        self.assertEqual(row['quantity'], '2.5')
        self.assertEqual(row['expiry_date'], '2027-01-02')

    def test_invalid_values_and_cross_tenant_sku_are_reported_per_row(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.client.post(self.url, {'rows': 'FOREIGN-SKU\t0\t-1\t\tbad-date\t'})
        row = response.json()['rows'][0]
        self.assertFalse(row['valid'])
        self.assertGreaterEqual(len(row['errors']), 4)
        self.assertIsNone(row['product'])

    def test_serialized_product_requires_unique_serial_per_whole_unit(self):
        self.client.login(username='paste-admin', password='pass')
        invalid = self.client.post(self.url, {'rows': 'PASTE-RAD\t2\t50\t\t\tABC,abc'}).json()['rows'][0]
        self.assertFalse(invalid['valid'])
        valid = self.client.post(self.url, {'rows': 'PASTE-RAD\t2\t50\t\t\tABC,DEF'}).json()['rows'][0]
        self.assertTrue(valid['valid'])
        self.assertEqual(valid['serial_numbers'], 'ABC\nDEF')

    def test_preview_is_non_mutating(self):
        self.client.login(username='paste-admin', password='pass')
        self.assertEqual(self.client.post(self.url, {'rows': 'PASTE-RTR\t2\t50'}).status_code, 200)
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_empty_and_more_than_two_hundred_rows_are_rejected(self):
        self.client.login(username='paste-admin', password='pass')
        self.assertEqual(self.client.post(self.url, {'rows': ''}).status_code, 400)
        rows = '\n'.join('PASTE-RTR\t1\t50' for _ in range(201))
        response = self.client.post(self.url, {'rows': rows})
        self.assertEqual(response.status_code, 400)
        self.assertIn('200', response.json()['error'])

    def test_workspace_exposes_paste_dialog_without_catalog_preload(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.client.get(reverse('inventory:purchase_create'))
        self.assertContains(response, 'data-open-paste-rows')
        self.assertContains(response, 'data-purchase-rows-preview-url')
        self.assertNotContains(response, 'PASTE-RTR')
