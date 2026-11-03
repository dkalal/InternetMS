import json
from decimal import Decimal
from io import BytesIO

from django.test import Client, TestCase
from django.urls import reverse
from openpyxl import Workbook

from . import tests_purchase_product_search as search_fixtures
from . import tests_cart_line_merge as cart_fixtures
from .imports import validate_workbook
from .models import ImportJob, StockUnit, StockMovement, Purchase
from .purchase_import import validate_rows
from users.models import TenantMembership, TenantPermissionGrant
from users.permissions import PermissionCode


class LiveSerialCheckTests(TestCase):
    make_product = search_fixtures.PurchaseProductSearchTests.make_product

    def setUp(self):
        search_fixtures.PurchaseProductSearchTests.setUp(self)
        self.url = reverse('inventory:serial_check')
        self.client.force_login(self.admin)
        self.unit_record = StockUnit.objects.create(tenant=self.organization, product=self.product, serial_number='001-ABC')
        StockUnit.objects.create(tenant=self.other_organization, product=self.other_product, serial_number='OTHER-SECRET')

    def check(self, serials, **params):
        return self.client.post(self.url, {'mode': 'purchase', 'serials': json.dumps(serials), **params})

    def test_tenant_scoped_read_only_feedback_includes_all_lifecycle_states(self):
        for state in (StockUnit.Status.AVAILABLE, StockUnit.Status.SOLD, StockUnit.Status.REMOVED):
            StockUnit.objects.filter(pk=self.unit_record.pk).update(status=state)
            response = self.check([' 001-abc ', 'OTHER-SECRET', 'NEW'])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'conflicts': ['001-ABC'], 'reason': 'exists'})
            self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(StockUnit.objects.count(), 2)
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(Purchase.objects.exists())

    def test_authentication_permissions_method_and_csrf_are_enforced(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.check(['001-ABC']).status_code, 302)
        self.client.force_login(self.sales)
        self.assertEqual(self.check(['001-ABC']).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.url, {'mode': 'purchase', 'serials': '[]'}).status_code, 403)

    def test_delegated_buyer_has_feedback_without_financial_access(self):
        member = TenantMembership.objects.get(tenant=self.organization, user=self.sales)
        grantor = TenantMembership.objects.get(tenant=self.organization, user=self.admin)
        TenantPermissionGrant.objects.create(membership=member, granted_by=grantor, action_code=PermissionCode.PURCHASES_CREATE)
        self.client.force_login(self.sales)
        self.assertEqual(self.check(['001-ABC']).json()['conflicts'], ['001-ABC'])
        self.assertEqual(self.check(['001-ABC'], mode='adjustment', direction='increase', product=self.product.pk).status_code, 403)

    def test_malformed_and_oversize_requests_fail_without_mutation(self):
        for payload in ('not-json', '{}', '[null]', '[1]', '[""]', json.dumps(['X\x00Y']), json.dumps(['X'] * 1001), json.dumps(['X' * 161])):
            self.assertEqual(self.client.post(self.url, {'mode': 'purchase', 'serials': payload}).status_code, 400)
        self.assertEqual(self.check([], mode='unknown').status_code, 400)
        self.assertEqual(StockUnit.objects.count(), 2)

    def test_decrease_accepts_existing_available_serial_and_rejects_unavailable(self):
        params = {'mode': 'adjustment', 'direction': 'decrease', 'product': self.product.pk}
        response = self.check(['001-abc', 'OTHER-SECRET', 'MISSING'], **params)
        self.assertEqual(response.json(), {'conflicts': ['MISSING', 'OTHER-SECRET'], 'reason': 'unavailable'})
        StockUnit.objects.filter(pk=self.unit_record.pk).update(status=StockUnit.Status.SOLD)
        self.assertEqual(self.check(['001-ABC'], **params).json()['conflicts'], ['001-ABC'])
        self.assertEqual(self.check(['001-ABC'], **{**params, 'product': self.other_product.pk}).status_code, 404)
        self.assertEqual(self.check(['001-ABC'], **{**params, 'product': 'not-an-id'}).status_code, 404)

    def test_purchase_preview_rejects_existing_serial_without_catalog_writes(self):
        rows = validate_rows([{'sku': self.product.sku, 'quantity': '1', 'unit_cost': '10',
                               'serial_numbers': '001-abc'}], organization=self.organization)
        self.assertFalse(rows[0]['valid'])
        self.assertIn('001-ABC', ' '.join(rows[0]['errors']))
        self.assertFalse(StockMovement.objects.exists())

    def test_opening_stock_preview_detects_duplicates_and_existing_serials(self):
        workbook = Workbook(); sheet = workbook.active
        sheet.append(['sku', 'quantity', 'serial_numbers', 'reason_notes'])
        sheet.append([self.product.sku, 2, 'NEW-001,new-001', 'Verified count'])
        sheet.append([self.product.sku, 2, 'NEW-001,001-ABC', 'Verified count'])
        output = BytesIO(); workbook.save(output); output.seek(0)
        job = validate_workbook(organization=self.organization, actor=self.admin, import_type='opening_stock', uploaded_file=output)
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertIn('NEW-001', str(job.errors)); self.assertIn('001-ABC', str(job.errors))
        self.assertFalse(StockMovement.objects.exists())


class CartLiveSerialSelectionTests(cart_fixtures.CartLineMergeFixture, TestCase):
    def test_add_picker_disables_serial_already_in_cart_but_edit_preserves_it(self):
        self.assertEqual(self.add(self.units[:1]).status_code, 302)
        response = self.client.get(self.url, {'product': self.product.pk})
        self.assertEqual(response.context['form'].serials_in_cart, {self.units[0].pk})
        self.assertContains(response, 'Already in this cart')
        line = self.sale.lines.get()
        edit = self.client.get(reverse('inventory:cart_line_edit', args=[self.sale.pk, line.pk]))
        self.assertEqual(edit.context['form'].serials_in_cart, set())


class InvoiceLiveSerialSelectionTests(TestCase):
    def test_duplicate_across_invoice_lines_is_rejected_and_corrected_selection_saves(self):
        from . import tests as fixtures
        from billing.models import BillingDocument
        from billing.services import BillingService, LineItemInput
        from .models import DocumentSerialSelection
        self.make_product = fixtures.InventoryAcceptanceTests.make_product.__get__(self)
        fixtures.InventoryAcceptanceTests.setUp(self)
        self.client.force_login(self.admin)
        product = self.make_product('Serialized invoice', 'INV-SERIAL', serialized=True)
        fixtures.InventoryAcceptanceTests.receive(self, product=product, quantity=2, serials='INV-001\nINV-002')
        invoice = BillingService.create_document(
            organization=self.org, created_by=self.admin, document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk, status=BillingDocument.Status.ISSUED,
            items=[LineItemInput(product_id=product.pk, quantity=Decimal('1')), LineItemInput(product_id=product.pk, quantity=Decimal('1'))],
        )
        lines = list(invoice.items.order_by('id'))
        self.assertEqual(len(lines), 2)
        units = list(StockUnit.objects.filter(product=product).order_by('serial_number'))
        url = reverse('inventory:invoice_serials', args=[invoice.pk])
        response = self.client.post(url, {f'line_{lines[0].pk}': units[0].pk, f'line_{lines[1].pk}': units[0].pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Each serial may be selected only once')
        self.assertFalse(DocumentSerialSelection.objects.exists())
        for invalid_id in ('not-an-id', '9' * 100, '-1'):
            response = self.client.post(url, {f'line_{lines[0].pk}': invalid_id, f'line_{lines[1].pk}': units[1].pk})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'unavailable')
            self.assertFalse(DocumentSerialSelection.objects.exists())
        response = self.client.post(url, {f'line_{lines[0].pk}': units[0].pk, f'line_{lines[1].pk}': units[1].pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DocumentSerialSelection.objects.count(), 2)
