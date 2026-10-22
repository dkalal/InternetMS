import json
import uuid
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import Workbook

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile, TenantMembership, TenantPermissionGrant
from users.permissions import PermissionCode

from .models import Purchase, PurchaseImportSubmission, PurchaseLine, StockMovement, Supplier


class MixedPurchaseImportTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='Mixed Tenant', slug='mixed-tenant')
        self.user = get_user_model().objects.create_user(username='mixed-admin', password='pass')
        UserAccessProfile.objects.create(user=self.user, tenant=self.tenant,
                                         role=UserAccessProfile.Role.TENANT_ADMIN)
        self.unit = UnitOfMeasure.objects.create(organization=self.tenant, tenant=self.tenant, name='Piece', symbol='pc')
        self.category = ProductCategory.objects.create(organization=self.tenant, tenant=self.tenant,
                                                       name='Devices', default_unit=self.unit)
        self.product = Product.objects.create(organization=self.tenant, tenant=self.tenant, name='Router',
                                              sku='000123', catalog_category=self.category, sales_unit=self.unit,
                                              quantity=Decimal('0'), buying_price=Decimal('0'), selling_price=Decimal('100'))
        self.supplier = Supplier.objects.create(organization=self.tenant, tenant=self.tenant,
                                                company_name='Supplier', created_by=self.user)
        self.client.force_login(self.user)

    def payload(self, rows, *, token=None, action='save_review'):
        return {
            'action': action, 'supplier': self.supplier.pk, 'reference_number': 'MIXED-1',
            'auto_generated_reference': '', 'purchase_date': date.today().isoformat(), 'notes': '',
            'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0', 'lines-MAX_NUM_FORMS': '1000',
            'import_token': str(token or uuid.uuid4()), 'import_rows': json.dumps(rows),
        }

    def rows(self):
        return [
            {'sku': '000123', 'quantity': '2', 'unit_cost': '12.345678', 'resolution': 'existing',
             'product_id': self.product.pk, 'expected_sku': '000123'},
            {'sku': '', 'product_name': 'New radio', 'quantity': '1', 'unit_cost': '10',
             'category': 'Devices', 'unit': 'Piece', 'selling_price': '30.00',
             'serialized': 'Yes', 'track_expiry': 'No', 'serial_numbers': '00001',
             'resolution': 'new', 'staging_reference': 'radio-1'},
        ]

    def test_custom_column_inspection_preview_and_save_use_existing_boundaries(self):
        from .tests_purchase_column_mapping import SAMPLE_TSV, EXPLICIT_MAPPING
        url = reverse('inventory:purchase_rows_preview')
        inspection = self.client.post(url, {'rows': SAMPLE_TSV, 'operation': 'inspect_columns'})
        self.assertEqual(inspection.status_code, 200)
        self.assertEqual(inspection.json()['column_mapping']['row_count'], 13)
        failed = self.client.post(url, {'rows': SAMPLE_TSV, 'format': 'mixed'})
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(failed.json()['code'], 'column_mapping_required')
        self.assertEqual(failed.json()['column_mapping']['column_count'], 7)
        preview = self.client.post(url, {'rows': SAMPLE_TSV, 'column_mapping': json.dumps(EXPLICIT_MAPPING),
                                        'has_header': 'false'})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()['format_label'], 'Custom column mapping')
        self.assertEqual(len(preview.json()['rows']), 13)
        self.assertEqual(preview.json()['rows'][0]['product_name'], 'TP-Link TL-SG1024')
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(PurchaseLine.objects.exists())
        self.assertEqual(Product.objects.count(), 1)
        self.assertFalse(StockMovement.objects.exists())
        # An explicit test mapping for an existing product, with an ignored source column.
        existing = self.client.post(url, {'rows': 'Router\t2\t12.345678\t000123\tignore',
                                         'column_mapping': json.dumps(['product_name', 'quantity', 'unit_cost', 'sku', '__ignore__']),
                                         'has_header': 'false'}).json()
        self.assertEqual(existing['ignored_columns'], 1)
        row = existing['rows'][0]
        self.assertEqual(row['product_id'], self.product.pk)
        self.assertEqual(self.client.post(reverse('inventory:purchase_create'), self.payload([row])).status_code, 302)
        self.assertEqual(Purchase.objects.get().total_cost, Decimal('24.69'))
        self.assertFalse(StockMovement.objects.exists())

    def test_custom_mapping_rejects_crafted_schema_without_writes(self):
        url = reverse('inventory:purchase_rows_preview')
        for supplied in ('null', '{}', '["sku", "tenant_id", "unit_cost"]', '{not json', 'X' * 2001):
            with self.subTest(supplied=supplied[:30]):
                response = self.client.post(url, {'rows': '000123\t1\t10', 'column_mapping': supplied, 'has_header': 'false'})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(url, {'rows': '000123\t1\t10', 'operation': 'save_products'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'rows': '000123\t1\t10', 'operation': 'inspect_columns', 'has_header': 'maybe'}).status_code, 400)
        self.assertFalse(Purchase.objects.exists())
        self.assertEqual(Product.objects.count(), 1)

    def test_column_inspection_enforces_purchase_permission_csrf_and_methods(self):
        from django.test import Client
        url = reverse('inventory:purchase_rows_preview')
        self.assertEqual(self.client.get(url, {'operation': 'inspect_columns'}).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url, {'rows': '000123\t1\t10', 'operation': 'inspect_columns'}).status_code, 403)
        buyer = get_user_model().objects.create_user(username='mapping-no-grants', password='pass')
        UserAccessProfile.objects.create(user=buyer, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_STAFF)
        self.client.force_login(buyer)
        self.assertEqual(self.client.post(url, {'rows': '000123\t1\t10', 'operation': 'inspect_columns'}).status_code, 403)

    def test_mapped_catalog_selection_is_still_tenant_scoped(self):
        other = Organization.objects.create(name='Mapping Other Tenant', slug='mapping-other')
        unit = UnitOfMeasure.objects.create(organization=other, tenant=other, name='Piece')
        category = ProductCategory.objects.create(organization=other, tenant=other, name='Devices', default_unit=unit)
        Product.objects.create(organization=other, tenant=other, name='Private device', sku='FOREIGN-SKU',
                               catalog_category=category, sales_unit=unit, quantity=Decimal('0'),
                               buying_price=Decimal('0'), selling_price=Decimal('20'))
        url = reverse('inventory:purchase_rows_preview')
        response = self.client.post(url, {'rows': 'Private device\t1\t10\tFOREIGN-SKU',
                                        'column_mapping': json.dumps(['product_name', 'quantity', 'unit_cost', 'sku']),
                                        'has_header': 'false'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['rows'][0]['product'])
        self.assertFalse(response.json()['rows'][0]['valid'])

    def test_two_hundred_mapped_rows_have_bounded_queries(self):
        url = reverse('inventory:purchase_rows_preview')
        mapping = json.dumps(['product_name', 'quantity', 'unit_cost', 'sku'])
        with CaptureQueriesContext(connection) as small:
            self.client.post(url, {'rows': 'Router\t1\t10\t000123', 'column_mapping': mapping, 'has_header': 'false'})
        with CaptureQueriesContext(connection) as large:
            response = self.client.post(url, {'rows': '\n'.join('Router\t1\t10\t000123' for _ in range(200)),
                                              'column_mapping': mapping, 'has_header': 'false'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['review_summary']['matched'], 200)
        self.assertLessEqual(len(large), len(small) + 1)
        self.assertFalse(Purchase.objects.exists())
        self.assertEqual(Product.objects.count(), 1)

    def test_mixed_preview_and_atomic_save_are_stock_neutral(self):
        text = ('sku\tproduct_name\tquantity\tunit_cost\tcategory\tunit\tselling_price\tbrand\tmodel_number\tserialized\ttrack_expiry\tbatch_reference\texpiry_date\tserial_numbers\tstaging_reference\n'
                '000123\tRouter\t2\t12.345678\n'
                '\tNew radio\t1\t10\tDevices\tPiece\t30.00\t\t\tYes\tNo\t\t\t00001\tradio-1')
        preview = self.client.post(reverse('inventory:purchase_rows_preview'), {'rows': text}).json()
        self.assertEqual(preview['summary']['matched'], 1)
        self.assertEqual(Product.objects.count(), 1)
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(self.rows()))
        self.assertEqual(response.status_code, 302)
        purchase = Purchase.objects.get(reference_number='MIXED-1')
        self.assertEqual(purchase.lines.count(), 2)
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(purchase.total_cost, Decimal('34.69'))
        self.assertFalse(StockMovement.objects.exists())

    def test_retry_and_changed_token_content(self):
        token = uuid.uuid4()
        payload = self.payload(self.rows(), token=token)
        first = self.client.post(reverse('inventory:purchase_create'), payload)
        second = self.client.post(reverse('inventory:purchase_create'), payload)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Purchase.objects.count(), 1)
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(PurchaseLine.objects.count(), 2)
        self.assertEqual(PurchaseImportSubmission.objects.count(), 1)
        changed = dict(payload)
        changed['notes'] = 'different'
        response = self.client.post(reverse('inventory:purchase_create'), changed)
        self.assertContains(response, 'already saved with different content')
        self.assertEqual(Purchase.objects.count(), 1)

    def test_invalid_new_product_rolls_back_purchase(self):
        rows = self.rows()
        rows[1]['selling_price'] = '0'
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(rows))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Import row 2')
        self.assertFalse(Purchase.objects.exists())
        self.assertEqual(Product.objects.count(), 1)

    def test_upload_and_paste_resolve_equally_with_text_sku(self):
        text = 'SKU\tQuantity\tUnit cost\tBatch reference\tExpiry\tSerial numbers\n000123\t1\t10'
        pasted = self.client.post(reverse('inventory:purchase_rows_preview'), {'rows': text}).json()['rows'][0]
        book = Workbook()
        sheet = book.active
        sheet.title = 'Purchase rows'
        sheet.append(['SKU', 'Quantity', 'Unit cost', 'Batch reference', 'Expiry', 'Serial numbers'])
        sheet.append(['000123', 1, 10, '', '', ''])
        output = BytesIO()
        book.save(output)
        output.seek(0)
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile('rows.xlsx', output.read())
        uploaded = self.client.post(reverse('inventory:purchase_rows_preview'), {'file': upload}).json()['rows'][0]
        self.assertEqual(pasted['product']['id'], uploaded['product']['id'])
        self.assertEqual(uploaded['sku'], '000123')

    def test_missing_sku_requires_explicit_existing_selection(self):
        row = {'sku': '', 'product_name': 'Router', 'quantity': '1', 'unit_cost': '10'}
        rejected = self.client.post(reverse('inventory:purchase_create'), self.payload([row]))
        self.assertContains(rejected, 'Choose an existing product or Create new')
        self.assertFalse(Purchase.objects.exists())
        row.update(resolution='existing', product_id=self.product.pk, expected_sku='000123')
        self.assertEqual(self.client.post(reverse('inventory:purchase_create'), self.payload([row])).status_code, 302)
        self.assertEqual(Product.objects.count(), 1)

    def test_unknown_sku_and_ineligible_catalog_product_are_not_guessed(self):
        unknown = self.client.post(reverse('inventory:purchase_rows_preview'), {'rows': 'NOT-KNOWN\t1\t5'}).json()['rows'][0]
        self.assertEqual(unknown['status'], 'unresolved')
        self.assertFalse(unknown['valid'])
        self.product.is_active = False
        self.product.save()
        ineligible = self.client.post(reverse('inventory:purchase_rows_preview'), {'rows': '000123\t1\t5'}).json()['rows'][0]
        self.assertFalse(ineligible['valid'])
        self.assertIn('inactive', ' '.join(ineligible['errors']))

    def test_conflicting_shared_definition_and_duplicate_serial_roll_back(self):
        rows = self.rows()
        rows.append(dict(rows[1], product_name='Different radio', serial_numbers='00002'))
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(rows))
        self.assertContains(response, 'conflicting product definitions')
        self.assertFalse(Purchase.objects.exists())
        rows[2]['product_name'] = 'New radio'
        rows[2]['serial_numbers'] = '00001'
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(rows))
        self.assertContains(response, 'occurs in another row')
        self.assertFalse(Purchase.objects.exists())

    def test_failure_after_first_product_creation_rolls_back_everything(self):
        rows = self.rows()
        second_new = dict(rows[1], staging_reference='radio-2', serial_numbers='00002')
        rows.append(second_new)
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(rows))
        self.assertContains(response, 'active product with this name already exists')
        self.assertEqual(Product.objects.count(), 1)
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(PurchaseLine.objects.exists())

    def test_approved_new_sku_is_preserved_and_duplicate_proposals_fail(self):
        rows = self.rows()
        rows[1]['sku'] = '000999'
        self.assertEqual(self.client.post(reverse('inventory:purchase_create'), self.payload(rows)).status_code, 302)
        self.assertEqual(Product.objects.get(name='New radio').sku, '000999')
        other = dict(rows[1], product_name='Another radio', staging_reference='radio-2',
                     serial_numbers='00002')
        review = self.client.post(reverse('inventory:purchase_rows_preview'),
                                  {'review_rows': json.dumps([rows[1], other])}).json()['rows']
        self.assertFalse(review[1]['valid'])
        self.assertIn('already proposed', ' '.join(review[1]['errors']))

    def test_existing_only_import_with_purchase_grant_and_no_product_creation_grant(self):
        buyer = get_user_model().objects.create_user(username='buyer', password='pass')
        UserAccessProfile.objects.create(user=buyer, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_STAFF)
        membership = TenantMembership.objects.get(user=buyer, tenant=self.tenant)
        grantor = TenantMembership.objects.get(user=self.user, tenant=self.tenant)
        for code in (PermissionCode.PURCHASES_CREATE,):
            TenantPermissionGrant.objects.create(membership=membership, action_code=code, granted_by=grantor)
        self.client.force_login(buyer)
        existing = [self.rows()[0]]
        self.assertEqual(self.client.post(reverse('inventory:purchase_create'), self.payload(existing)).status_code, 302)
        created = Purchase.objects.get(reference_number='MIXED-1')
        self.assertEqual(self.client.get(reverse('inventory:purchase_detail', args=[created.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('inventory:purchase_confirm', args=[created.pk])).status_code, 403)
        self.assertEqual(Product.objects.count(), 1)
        second_payload = self.payload([self.rows()[1]])
        second_payload['reference_number'] = 'MIXED-2'
        rejected = self.client.post(reverse('inventory:purchase_create'), second_payload)
        self.assertContains(rejected, 'product creation permission')
        self.assertEqual(Product.objects.count(), 1)

    def test_stale_catalog_sku_preserves_batch(self):
        rows = [self.rows()[0]]
        self.product.sku = 'CHANGED'
        self.product.save()
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(rows))
        self.assertContains(response, 'changed after preview')
        self.assertContains(response, 'name="import_rows"')
        self.assertFalse(Purchase.objects.exists())

    def test_two_hundred_row_preview_has_bounded_queries_and_no_writes(self):
        text = '\n'.join('000123\t1\t10' for _ in range(200))
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(reverse('inventory:purchase_rows_preview'), {'rows': text})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['valid'], 200)
        self.assertLess(len(queries), 20)
        self.assertFalse(Purchase.objects.exists())
        self.assertEqual(Product.objects.count(), 1)
        proposed = [dict(self.rows()[1], product_name=f'New item {index}', serialized='No',
                         serial_numbers='', staging_reference=f'new-{index}') for index in range(200)]
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(reverse('inventory:purchase_rows_preview'),
                                        {'review_rows': json.dumps(proposed)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['new'], 200)
        self.assertLess(len(queries), 20)

    def test_source_rows_survive_review_and_excluded_serials_do_not_conflict(self):
        rows = [dict(self.rows()[1], row=17, excluded=True),
                dict(self.rows()[1], row=21, staging_reference='other-radio')]
        preview = self.client.post(reverse('inventory:purchase_rows_preview'),
                                   {'review_rows': json.dumps(rows)}).json()['rows']
        self.assertEqual([row['row'] for row in preview], [17, 21])
        self.assertTrue(preview[1]['valid'], preview[1]['errors'])

    def test_reordered_headers_blank_sku_and_formatted_prices_can_save(self):
        text = ('Product name\tSKU\tUnit cost\tQuantity\tCategory\tUnit\tSelling price\n'
                'New radio\t\t1,250.123456\t2\tDevices\tPiece\t2,500.00')
        preview = self.client.post(reverse('inventory:purchase_rows_preview'), {'rows': text})
        self.assertEqual(preview.status_code, 200)
        row = preview.json()['rows'][0]
        self.assertEqual(row['quantity'], '2')
        row['resolution'] = 'new'
        reviewed = self.client.post(reverse('inventory:purchase_rows_preview'),
                                    {'review_rows': json.dumps([row])}).json()['rows']
        self.assertTrue(reviewed[0]['valid'], reviewed[0]['errors'])
        self.assertEqual(self.client.post(reverse('inventory:purchase_create'), self.payload(reviewed)).status_code, 302)
        self.assertEqual(Purchase.objects.get().total_cost, Decimal('2500.25'))
        self.assertEqual(Product.objects.get(name='New radio').selling_price, Decimal('2500.00'))

    def test_cross_tenant_selected_product_is_rejected_without_disclosure(self):
        other = Organization.objects.create(name='Other Mixed', slug='other-mixed')
        unit = UnitOfMeasure.objects.create(organization=other, tenant=other, name='Piece')
        category = ProductCategory.objects.create(organization=other, tenant=other, name='Devices', default_unit=unit)
        foreign = Product.objects.create(organization=other, tenant=other, name='Private item', sku='FOREIGN',
                                         catalog_category=category, sales_unit=unit, quantity=Decimal('0'),
                                         buying_price=Decimal('0'), selling_price=Decimal('20'))
        row = {'sku': '', 'product_name': 'Private item', 'quantity': '1', 'unit_cost': '5',
               'resolution': 'existing', 'product_id': foreign.pk}
        preview = self.client.post(reverse('inventory:purchase_rows_preview'),
                                   {'review_rows': json.dumps([row])}).json()['rows'][0]
        self.assertFalse(preview['valid'])
        self.assertIsNone(preview['product'])
        response = self.client.post(reverse('inventory:purchase_create'), self.payload([row]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Purchase.objects.exists())

    def test_formula_and_invalid_new_category_are_rejected(self):
        formula = self.client.post(reverse('inventory:purchase_rows_preview'),
                                   {'rows': '000123\t1\t=1+1'}).json()['rows'][0]
        self.assertIn('Formula', ' '.join(formula['errors']))
        rows = self.rows()
        rows[1]['category'] = 'Foreign category'
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(rows))
        self.assertContains(response, 'active category')
        self.assertFalse(Purchase.objects.exists())

    def test_editing_draft_does_not_reappend_an_applied_batch(self):
        self.client.post(reverse('inventory:purchase_create'), self.payload([self.rows()[0]]))
        purchase = Purchase.objects.get(reference_number='MIXED-1')
        original = purchase.lines.get()
        payload = self.payload([self.rows()[1]])
        payload['lines-TOTAL_FORMS'] = '1'
        payload['lines-INITIAL_FORMS'] = '1'
        payload.update({
            'lines-0-id': original.pk, 'lines-0-product': original.product_id,
            'lines-0-quantity': str(original.quantity), 'lines-0-unit_cost': str(original.unit_cost),
            'lines-0-batch_reference': '', 'lines-0-expiry_date': '', 'lines-0-serial_numbers': '',
        })
        edit_url = reverse('inventory:purchase_edit', args=[purchase.pk])
        self.assertEqual(self.client.post(edit_url, payload).status_code, 302)
        self.assertEqual(self.client.post(edit_url, payload).status_code, 302)
        self.assertEqual(purchase.lines.count(), 2)
        self.assertEqual(Product.objects.count(), 2)
