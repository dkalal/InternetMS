from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from .models import InventoryBalance, Purchase, PurchaseLine, StockMovement, Supplier


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
        self.supplier = Supplier.objects.create(
            organization=self.tenant, tenant=self.tenant, company_name='Paste Supplier', created_by=self.admin,
        )

    def preview(self, text):
        return self.client.post(self.url, {'rows': text})

    def test_authentication_post_and_csrf_are_required(self):
        self.assertEqual(self.client.post(self.url, {'rows': 'PASTE-RTR\t2\t50'}).status_code, 302)
        self.client.login(username='paste-admin', password='pass')
        self.assertEqual(self.client.get(self.url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username='paste-admin', password='pass')
        self.assertEqual(csrf_client.post(self.url, {'rows': 'PASTE-RTR\t2\t50'}).status_code, 403)

    def test_staff_without_purchase_permission_receives_403(self):
        staff = User.objects.create_user(username='paste-staff', password='pass')
        UserAccessProfile.objects.create(
            user=staff, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_STAFF,
        )
        self.client.login(username='paste-staff', password='pass')
        self.assertEqual(self.preview('PASTE-RTR\t1\t50').status_code, 403)

    def purchase_payload(self, *, action='save_continue', serials=None):
        lines = [
            (self.product.pk, '2.5', '10.25', 'MANUAL', '', ''),
            (self.product.pk, '3', '20', 'PASTED', '2027-01-02', ''),
            (self.serialized.pk, '2', '30', 'SERIAL', '', serials or 'SER-A\nSER-B'),
        ]
        payload = {
            'action': action, 'supplier': self.supplier.pk, 'reference_number': 'PASTE-WORKSPACE-1',
            'auto_generated_reference': '', 'purchase_date': date.today().isoformat(),
            'notes': 'Preserved notes', 'lines-TOTAL_FORMS': str(len(lines)),
            'lines-INITIAL_FORMS': '0', 'lines-MIN_NUM_FORMS': '0', 'lines-MAX_NUM_FORMS': '1000',
        }
        for index, (product, quantity, cost, batch, expiry, serial) in enumerate(lines):
            payload.update({
                f'lines-{index}-product': product, f'lines-{index}-quantity': quantity,
                f'lines-{index}-unit_cost': cost, f'lines-{index}-batch_reference': batch,
                f'lines-{index}-expiry_date': expiry, f'lines-{index}-serial_numbers': serial,
            })
        return payload

    def test_workspace_save_preserves_manual_and_pasted_values_and_total(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.client.post(reverse('inventory:purchase_create'), self.purchase_payload())
        purchase = Purchase.objects.get(reference_number='PASTE-WORKSPACE-1')
        self.assertRedirects(response, reverse('inventory:purchase_edit', args=[purchase.pk]))
        self.assertEqual(purchase.supplier_id, self.supplier.pk)
        self.assertEqual(purchase.notes, 'Preserved notes')
        self.assertEqual(purchase.purchase_date, date.today())
        self.assertEqual(purchase.total_cost, Decimal('145.62'))
        self.assertEqual(list(purchase.lines.order_by('pk').values_list(
            'batch_reference', 'expiry_date', 'serial_numbers',
        )), [
            ('MANUAL', None, ''), ('PASTED', date(2027, 1, 2), ''), ('SERIAL', None, 'SER-A\nSER-B'),
        ])
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(InventoryBalance.objects.exists())

    def test_save_review_and_receive_are_separate(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.client.post(
            reverse('inventory:purchase_create'), self.purchase_payload(action='save_review'),
        )
        purchase = Purchase.objects.get(reference_number='PASTE-WORKSPACE-1')
        self.assertRedirects(response, reverse('inventory:purchase_detail', args=[purchase.pk]))
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertFalse(StockMovement.objects.exists())

    def test_duplicate_serials_across_lines_fail_at_confirm_without_stock_change(self):
        self.client.login(username='paste-admin', password='pass')
        payload = self.purchase_payload(action='save_review')
        payload['lines-TOTAL_FORMS'] = '4'
        payload.update({
            'lines-3-product': self.serialized.pk, 'lines-3-quantity': '1',
            'lines-3-unit_cost': '30', 'lines-3-batch_reference': 'SECOND',
            'lines-3-expiry_date': '', 'lines-3-serial_numbers': 'ser-a',
        })
        self.client.post(reverse('inventory:purchase_create'), payload)
        purchase = Purchase.objects.get(reference_number='PASTE-WORKSPACE-1')
        response = self.client.post(reverse('inventory:purchase_confirm', args=[purchase.pk]))
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(InventoryBalance.objects.exists())

    def test_valid_rows_and_optional_header_are_normalized(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.client.post(self.url, {'rows': 'SKU\tQuantity\tUnit cost\tBatch\tExpiry\tSerials\nPASTE-RTR\t2.5\t75.25\tB-1\t2027-01-02\t'})
        self.assertEqual(response.status_code, 200)
        row = response.json()['rows'][0]
        self.assertTrue(row['valid'])
        self.assertEqual(row['product']['id'], self.product.pk)
        self.assertEqual(row['quantity'], '2.5')
        self.assertEqual(row['expiry_date'], '2027-01-02')

    def test_headerless_lowercase_sku_blank_rows_and_spreadsheet_quotes(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.preview('\nPASTE-RTR\t1\t0\t"Batch\tA"\t\t\n\npaste-rtr\t2\t50\tB-2\t\t')
        self.assertEqual(response.status_code, 200)
        rows = response.json()['rows']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['valid'] for row in rows))
        self.assertEqual(rows[0]['batch_reference'], 'Batch\tA')
        self.assertEqual(rows[1]['product']['id'], self.product.pk)

    def test_mixed_tab_and_space_separated_three_column_rows(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.preview('PASTE-RTR\t1\t50\npaste-rtr  2  0\nPASTE-RTR    3    25.5')
        self.assertEqual(response.status_code, 200)
        rows = response.json()['rows']
        self.assertEqual(response.json()['summary'], {'total': 3, 'valid': 3})
        self.assertEqual([row['quantity'] for row in rows], ['1', '2', '3'])
        self.assertEqual([row['unit_cost'] for row in rows], ['50', '0', '25.5'])
        self.assertTrue(all(row['product']['id'] == self.product.pk for row in rows))

    def test_single_space_separated_row_resolves_sku_without_any_tab_row(self):
        self.client.login(username='paste-admin', password='pass')
        row = self.preview('PASTE-RTR 1 50').json()['rows'][0]
        self.assertTrue(row['valid'], row['errors'])
        self.assertEqual(row['product']['id'], self.product.pk)

    def test_space_separated_batch_and_expiry_and_incomplete_rows(self):
        self.client.login(username='paste-admin', password='pass')
        rows = self.preview('\n'.join((
            'PASTE-RTR 10 250000 BATCH 2026-09-09',
            'PASTE-RTR 1 250000',
            'UNKNOWN-SKU 3 2500000',
            'PASTE-RTR',
        ))).json()['rows']
        self.assertTrue(rows[0]['valid'], rows[0]['errors'])
        self.assertEqual(rows[0]['batch_reference'], 'BATCH')
        self.assertEqual(rows[0]['expiry_date'], '2026-09-09')
        self.assertTrue(rows[1]['valid'], rows[1]['errors'])
        self.assertFalse(rows[2]['valid'])
        self.assertIn('No active stock product', rows[2]['errors'][0])
        self.assertFalse(rows[3]['valid'])
        self.assertEqual(rows[3]['product']['id'], self.product.pk)
        self.assertIn('Quantity is required.', rows[3]['errors'])
        self.assertIn('Unit cost is required.', rows[3]['errors'])

    def test_more_than_six_space_separated_columns_are_rejected(self):
        self.client.login(username='paste-admin', password='pass')
        row = self.preview('PASTE-RTR 1 50 BATCH 2026-09-09 A B').json()['rows'][0]
        self.assertFalse(row['valid'])
        self.assertIn('Use no more than six columns.', row['errors'])

    def test_unknown_inactive_service_and_untracked_skus_are_rejected(self):
        self.client.login(username='paste-admin', password='pass')
        for sku, changes in (
            ('INACTIVE', {'is_active': False}),
            ('SERVICE', {'item_type': Product.ItemType.SERVICE, 'track_stock': False}),
            ('UNTRACKED', {'track_stock': False}),
        ):
            Product.objects.create(
                organization=self.tenant, tenant=self.tenant, name=sku, sku=sku,
                catalog_category=self.product.catalog_category, sales_unit=self.product.sales_unit,
                quantity=Decimal('0'), stock=0, buying_price=Decimal('50'), selling_price=Decimal('100'),
                **changes,
            )
        rows = self.preview('\n'.join(f'{sku}\t1\t50' for sku in (
            'UNKNOWN', 'INACTIVE', 'SERVICE', 'UNTRACKED', 'FOREIGN-SKU',
        ))).json()['rows']
        self.assertTrue(all(not row['valid'] and row['product'] is None for row in rows))

    def test_invalid_decimal_expiry_serial_and_extra_columns_fail_safely(self):
        self.client.login(username='paste-admin', password='pass')
        rows = self.preview('\n'.join((
            'PASTE-RTR\t0\t-1\t\t2027-02-30',
            'PASTE-RTR\tNaN\tInfinity',
            'PASTE-RAD\t999999999999999999999999999999\t50\t\t\tA',
            'PASTE-RTR\t1.0000001\t50',
            'PASTE-RTR\t1\t50.0000001',
            'PASTE-RAD\t1.5\t50\t\t\tA,B',
            'PASTE-RAD\t2\t50\t\t\tA,a',
            'PASTE-RTR\t1\t50\t\t\t\tunexpected',
        ))).json()['rows']
        self.assertTrue(all(not row['valid'] for row in rows))

    def test_malformed_quoted_input_is_rejected_cleanly(self):
        self.client.login(username='paste-admin', password='pass')
        response = self.preview('PASTE-RTR\t1\t50\t"unterminated')
        self.assertEqual(response.status_code, 400)
        self.assertIn('quoting', response.json()['error'])

    def test_character_limit_and_exact_two_hundred_rows(self):
        self.client.login(username='paste-admin', password='pass')
        self.assertEqual(self.preview('X' * 100_001).status_code, 400)
        response = self.preview('\n'.join('PASTE-RTR\t1\t50' for _ in range(200)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary'], {'total': 200, 'valid': 200})

    def test_catalog_lookup_query_count_stays_bounded(self):
        self.client.login(username='paste-admin', password='pass')
        with CaptureQueriesContext(connection) as small:
            self.preview('PASTE-RTR\t1\t50')
        with CaptureQueriesContext(connection) as large:
            self.preview('\n'.join('PASTE-RTR\t1\t50' for _ in range(200)))
        self.assertLessEqual(len(large), len(small) + 1)

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
        before_products = Product.objects.count()
        before_balance = InventoryBalance.objects.count()
        self.assertEqual(self.client.post(self.url, {'rows': 'PASTE-RTR\t2\t50'}).status_code, 200)
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(PurchaseLine.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertEqual(Product.objects.count(), before_products)
        self.assertEqual(InventoryBalance.objects.count(), before_balance)

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
