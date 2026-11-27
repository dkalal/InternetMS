from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from .tests_packaging_receipts import ReceiptFixture
from . import tests_packaging_sales as sale_tests
from .models import InventoryBalance, StockContainer, ContainerAllocation, ContainerStockEvent, StockMovement
from .packaging_corrections import ContainerCorrectionService


@patch('products.packaging.workflows_ready', return_value=True)
class PackagingCorrectionTests(ReceiptFixture, TestCase):
    invoice = sale_tests.PackagingSaleTests.invoice
    pay = sale_tests.PackagingSaleTests.pay

    def setUp(self):
        super().setUp()
        with patch('products.packaging.workflows_ready', return_value=True):
            self.line(); self.receive()

    def returned(self, source, quantity='20', **overrides):
        values = dict(organization=self.org, actor=self.actor, allocation_id=source.pk, quantity=quantity,
            request_key=str(uuid4()), reason='Inspected physical cable return')
        values.update(overrides)
        return ContainerCorrectionService.return_stock(**values)

    def test_ten_box_sales_and_physical_return_reconcile_end_to_end(self, ready):
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))
        self.assertEqual(StockContainer.objects.filter(state='sealed').count(), 10)
        self.pay(self.invoice('2', package=True))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2440'))
        self.assertEqual(StockContainer.objects.filter(state='sealed').count(), 8)
        self.pay(self.invoice('50'))
        opened = StockContainer.objects.get(state='opened')
        self.assertEqual(opened.remaining_quantity, Decimal('255'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2390'))
        final_invoice = self.invoice('300')
        receipt = self.pay(final_invoice)
        self.assertEqual(self.pay(final_invoice).pk, receipt.pk)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2090'))
        self.assertEqual(StockContainer.objects.filter(state='sealed').count(), 6)
        self.assertEqual(StockContainer.objects.get(state='opened').remaining_quantity, Decimal('260'))
        source = ContainerAllocation.objects.get(billing_line__document=final_invoice, container=opened)
        self.assertEqual(source.base_quantity, Decimal('255'))
        event = self.returned(source, quantity='20')
        self.assertEqual(event.container.state, 'returned_piece')
        self.assertNotEqual(event.container_id, opened.pk)
        opened.refresh_from_db()
        self.assertEqual(opened.remaining_quantity, Decimal('0'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2110'))
        self.assertEqual(event.container.remaining_quantity, Decimal('20'))

    def test_partial_return_creates_separate_piece_restores_original_cost_and_retries_once(self, ready):
        invoice = self.invoice('50'); self.pay(invoice)
        source = ContainerAllocation.objects.get()
        original = source.container
        remaining = original.remaining_quantity
        key = str(uuid4())
        event = self.returned(source, request_key=key)
        self.assertEqual(self.returned(source, request_key=key).pk, event.pk)
        self.assertEqual(event.cost_total, Decimal('20'))
        self.assertEqual(event.container.state, 'returned_piece')
        self.assertEqual(event.container.remaining_quantity, Decimal('20'))
        original.refresh_from_db()
        self.assertEqual(original.remaining_quantity, remaining)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3020'))
        self.assertEqual(invoice.receipts.count(), 1)
        self.assertEqual(ContainerStockEvent.objects.count(), 1)
        # A returned piece is separate and can be selected for a later base-unit sale.
        subsequent = self.invoice('20', selections=[(event.container.pk, Decimal('20'))])
        self.pay(subsequent)
        event.container.refresh_from_db()
        self.assertEqual(event.container.state, 'depleted')

    def test_full_return_is_never_a_sealed_package(self, ready):
        self.pay(self.invoice('1', package=True))
        source = ContainerAllocation.objects.get()
        event = self.returned(source, quantity='305')
        self.assertEqual(event.container.state, 'returned_full')
        from billing.services import BillingServiceError
        with self.assertRaises(BillingServiceError):
            self.invoice('1', package=True, selections=[(event.container.pk, Decimal('305'))])
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))

    def test_cumulative_returns_and_changed_retry_cannot_exceed_sale(self, ready):
        self.pay(self.invoice('50'))
        source = ContainerAllocation.objects.get()
        key = str(uuid4())
        self.returned(source, quantity='30', request_key=key)
        with self.assertRaises(ValidationError):
            self.returned(source, quantity='21')
        with self.assertRaises(ValidationError):
            self.returned(source, quantity='20', request_key=key)
        self.returned(source, quantity='20')
        self.assertEqual(sum(ContainerStockEvent.objects.values_list('cost_total', flat=True)), source.cost_total)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))

    def test_wastage_decreases_one_piece_and_is_atomic_idempotent_immutable(self, ready):
        container = StockContainer.objects.order_by('pk').first()
        values = dict(organization=self.org, actor=self.actor, container_id=container.pk, quantity='5',
            request_key=str(uuid4()), reason='Measured damaged cable', category='damaged')
        event = ContainerCorrectionService.waste(**values)
        self.assertEqual(ContainerCorrectionService.waste(**values).pk, event.pk)
        container.refresh_from_db()
        self.assertEqual(container.remaining_quantity, Decimal('300'))
        self.assertEqual(container.state, 'opened')
        self.assertEqual(event.cost_total, Decimal('5'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3045'))
        with self.assertRaises(ValidationError): event.save()
        with self.assertRaises(ValidationError): event.delete()
        values.update(quantity='301', request_key=str(uuid4()))
        with self.assertRaises(ValidationError): ContainerCorrectionService.waste(**values)
        self.assertEqual(ContainerStockEvent.objects.count(), 1)

    def test_foreign_source_and_unauthorized_actor_do_not_mutate_stock(self, ready):
        self.pay(self.invoice('50'))
        source = ContainerAllocation.objects.get()
        with self.assertRaises((ValidationError, PermissionDenied)):
            self.returned(source, organization=self.other)
        from django.contrib.auth import get_user_model
        outsider = get_user_model().objects.create_user(username='no-return-permission')
        with self.assertRaises(PermissionDenied): self.returned(source, actor=outsider)
        self.assertEqual(ContainerStockEvent.objects.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3000'))

    def test_failure_rolls_back_return_balance_container_movement_and_event(self, ready):
        self.pay(self.invoice('50'))
        source = ContainerAllocation.objects.get()
        count = StockMovement.objects.count()
        with patch('inventory.packaging_corrections.audit', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaises(RuntimeError): self.returned(source)
        self.assertEqual(ContainerStockEvent.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), count)
        self.assertEqual(StockContainer.objects.count(), 10)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3000'))

    def test_report_and_export_preserve_commercial_units_and_reconcile_base_stock(self, ready):
        from .reporting import report_rows
        from django.urls import reverse
        self.pay(self.invoice('2', package=True))
        self.pay(self.invoice('50'))
        headers, rows = report_rows(self.org, 'sales')
        self.assertEqual(sum(row[4] for row in rows), Decimal('660'))
        self.assertIn('Commercial unit', headers)
        self.assertEqual({row[headers.index('Commercial unit')] for row in rows}, {'Box', 'm'})
        headers, rows = report_rows(self.org, 'container-reconciliation')
        self.assertEqual(rows[0][3], Decimal('2390'))
        self.assertEqual(rows[0][4], Decimal('2390'))
        self.assertEqual(rows[0][5], Decimal('0'))
        self.assertEqual(rows[0][6], 7)
        self.client.force_login(self.actor)
        response = self.client.get(reverse('inventory:report', args=['container-reconciliation']) + '?export=xlsx')
        self.assertEqual(response.status_code, 200)
        from openpyxl import load_workbook
        from io import BytesIO
        book = load_workbook(BytesIO(response.content))
        self.assertEqual(book.active.cell(2, 4).value, 2390)
        self.assertEqual(book.active.cell(2, 6).value, 0)

    def test_commercial_purchase_import_is_explicit_stock_neutral_and_retry_safe(self, ready):
        from datetime import date
        from .packaging_import import import_commercial_purchase
        rows = [dict(sku=self.product.sku, packaging_id=str(self.package.pk), configuration_version=str(self.product.packaging_version),
            quantity='2', unit_cost='305', batch_reference='', expiry_date='')]
        values = dict(organization=self.org, actor=self.actor, supplier_id=self.supplier.pk, purchase_date=date.today(),
            rows=rows, request_key=str(uuid4()))
        draft = import_commercial_purchase(**values)
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(draft.lines.get().quantity, Decimal('610'))
        self.assertEqual(draft.lines.get().entered_quantity, Decimal('2'))
        self.assertEqual(draft.total_cost, Decimal('610'))
        self.assertEqual(import_commercial_purchase(**values).pk, draft.pk)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))
        rows[0]['quantity'] = '3'
        with self.assertRaises(ValidationError): import_commercial_purchase(**values)
        rows[0]['configuration_version'] = '999'
        values['request_key'] = str(uuid4())
        with self.assertRaises(ValidationError): import_commercial_purchase(**values)

    def test_commercial_workbook_rejects_formula_or_forged_columns(self, ready):
        from openpyxl import Workbook
        from django.core.files.uploadedfile import SimpleUploadedFile
        from io import BytesIO
        from .packaging_import import HEADERS, parse_commercial_workbook
        for headers, row in [(HEADERS, ['=1+1', '', '2', '1', '1', '', '']), (HEADERS + ('factor',), ['SKU', '', '2', '1', '1', '', '', '305'])]:
            book = Workbook(); book.active.title = 'Commercial purchase v1'
            book.active.append(list(headers)); book.active.append(row)
            out = BytesIO(); book.save(out)
            with self.assertRaises(ValidationError):
                parse_commercial_workbook(SimpleUploadedFile('commercial.xlsx', out.getvalue()))

    def test_unit_audit_does_not_treat_valid_box_snapshot_as_base_unit_drift(self, ready):
        from django.core.management import call_command
        from io import StringIO
        self.invoice('1', package=True)
        output = StringIO()
        call_command('audit_product_units', fail_on_drift=True, stdout=output)
        self.assertIn('unit_drift_lines=0', output.getvalue())

    def test_preflight_detects_quantity_difference_without_mutation(self, ready):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        self.pay(self.invoice('50'))
        source = ContainerAllocation.objects.get()
        self.returned(source)
        output = StringIO()
        call_command('audit_product_packaging', tenant=self.org.slug, fail_on_drift=True, stdout=output)
        self.assertIn('packaging_differences=0', output.getvalue())
        InventoryBalance.objects.filter(product=self.product).update(quantity=Decimal('3021'))
        with self.assertRaises(CommandError):
            call_command('audit_product_packaging', tenant=self.org.slug, fail_on_drift=True, stdout=StringIO())
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3021'))

    def test_base_stock_limit_is_rejected_before_legacy_integer_overflow(self, ready):
        from datetime import date
        from .models import Purchase
        from .services import InventoryError
        self.purchase = Purchase.objects.create(tenant=self.org, organization=self.org, supplier=self.supplier,
            reference_number='OVERFLOW-RECEIPT', purchase_date=date.today(), created_by=self.actor)
        self.line(quantity='3000000000', price='0', package=False)
        with self.assertRaises(InventoryError): self.receive()
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))
        self.assertEqual(StockContainer.objects.count(), 10)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, 'draft')

    def test_historical_receipt_print_keeps_box_quantity_after_price_change(self, ready):
        from products.packaging import PackagingService
        from django.urls import reverse
        receipt = self.pay(self.invoice('1', package=True))
        PackagingService.update_prices(organization=self.org, actor=self.actor, product_id=self.product.pk,
            packaging_id=self.package.pk, prices={'standard_price': '950'})
        self.client.force_login(self.actor)
        response = self.client.get(reverse('billing:receipt_print', args=[receipt.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 Box')
        self.assertNotContains(response, '305 Box')

    def test_unpaid_void_releases_only_provisional_pieces(self, ready):
        from billing.services import BillingService
        invoice = self.invoice('1', package=True)
        self.assertEqual(invoice.items.get().container_selections.count(), 1)
        BillingService.void_invoice(organization=self.org, performed_by=self.actor, invoice_id=invoice.pk,
            reason='Customer cancelled before dispatch')
        self.assertEqual(invoice.items.get().container_selections.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))
        self.assertEqual(ContainerAllocation.objects.count(), 0)

    def test_restricted_staff_cannot_configure_correct_or_export_costs(self, ready):
        from django.contrib.auth import get_user_model
        from users.models import UserAccessProfile
        from django.urls import reverse
        staff = get_user_model().objects.create_user(username='restricted-packaging-staff')
        UserAccessProfile.objects.create(user=staff, tenant=self.org, role=UserAccessProfile.Role.TENANT_STAFF)
        self.client.force_login(staff)
        for url in [reverse('product-packaging', args=[self.product.pk]),
                    reverse('inventory:container_waste', args=[StockContainer.objects.first().pk]),
                    reverse('inventory:report', args=['container-reconciliation']),
                    reverse('inventory:export_records', args=['packaging'])]:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(ContainerStockEvent.objects.count(), 0)

    def test_return_also_requires_visibility_of_original_invoice(self, ready):
        self.pay(self.invoice('50'))
        source = ContainerAllocation.objects.get()
        with patch('users.permissions.can_view_sales_document', return_value=False), self.assertRaises(PermissionDenied):
            self.returned(source)
        self.assertEqual(ContainerStockEvent.objects.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3000'))

    def test_correction_constraint_race_returns_actionable_error_and_rolls_back(self, ready):
        from django.db import IntegrityError
        container = StockContainer.objects.order_by('pk').first()
        with patch('inventory.packaging_corrections.ContainerStockEvent.objects.create', side_effect=IntegrityError('Synthetic key race')):
            with self.assertRaisesMessage(ValidationError, 'conflicts with another request'):
                ContainerCorrectionService.waste(organization=self.org, actor=self.actor, container_id=container.pk,
                    quantity='5', request_key=str(uuid4()), reason='Measured damaged end')
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))
        container.refresh_from_db()
        self.assertEqual(container.remaining_quantity, Decimal('305'))

    def test_invalid_stored_average_is_rejected_before_payment_or_stock(self, ready):
        from django.db import connection
        from billing.services import BillingServiceError
        invoice = self.invoice('1', package=True)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE inventory_inventorybalance SET average_cost='NaN'::numeric WHERE product_id=%s", [self.product.pk])
        with self.assertRaisesMessage(BillingServiceError, 'Inventory valuation requires reconciliation'): self.pay(invoice)
        self.assertEqual(invoice.receipts.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))
        self.assertEqual(ContainerAllocation.objects.count(), 0)


@patch('products.packaging.workflows_ready', return_value=True)
class PackagingRoundingTests(ReceiptFixture, TestCase):
    invoice = sale_tests.PackagingSaleTests.invoice
    pay = sale_tests.PackagingSaleTests.pay

    def test_final_depletion_and_partial_returns_preserve_cent_residual(self, ready):
        from .models import InventorySaleLine
        self.line(quantity='1', price='0.01'); self.receive()
        for amount in ['1', '1', '303']:
            self.pay(self.invoice(amount))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('0'))
        self.assertEqual(sum(InventorySaleLine.objects.values_list('cost_total', flat=True)), Decimal('0.01'))
        self.assertEqual(sum(ContainerAllocation.objects.values_list('cost_total', flat=True)), Decimal('0.01'))
        source = ContainerAllocation.objects.get(base_quantity=303)
        for amount in ['100', '203']:
            ContainerCorrectionService.return_stock(organization=self.org, actor=self.actor, allocation_id=source.pk,
                quantity=amount, request_key=str(uuid4()), reason='Verified separate returned cable piece')
        self.assertEqual(sum(ContainerStockEvent.objects.values_list('cost_total', flat=True)), Decimal('0.01'))
        balance = InventoryBalance.objects.get(product=self.product)
        self.assertEqual(balance.quantity, Decimal('303'))
        self.assertEqual(balance.total_value, Decimal('0.01'))
        self.assertEqual(StockContainer.objects.filter(state='returned_piece').count(), 2)


from concurrent.futures import ThreadPoolExecutor
from django.db import connections, close_old_connections
from django.test import TransactionTestCase


@patch('products.packaging.workflows_ready', return_value=True)
class PackagingCorrectionConcurrencyTests(ReceiptFixture, TransactionTestCase):
    invoice = sale_tests.PackagingSaleTests.invoice
    pay = sale_tests.PackagingSaleTests.pay

    def test_simultaneous_returns_cannot_over_return_original_sale(self, ready):
        self.line(); self.receive(); self.pay(self.invoice('50'))
        allocation = ContainerAllocation.objects.get()
        def returned(_):
            close_old_connections()
            try:
                ContainerCorrectionService.return_stock(organization=self.org, actor=self.actor,
                    allocation_id=allocation.pk, quantity='30', request_key=str(uuid4()), reason='Inspected physical return')
                return True
            except ValidationError:
                return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(returned, range(2)))
        self.assertEqual(sum(result), 1)
        self.assertEqual(ContainerStockEvent.objects.count(), 1)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3030'))

    def test_simultaneous_wastage_retry_posts_exactly_once(self, ready):
        self.line(); self.receive()
        container = StockContainer.objects.order_by('pk').first()
        key = str(uuid4())
        def wasted(_):
            close_old_connections()
            try:
                return ContainerCorrectionService.waste(organization=self.org, actor=self.actor,
                    container_id=container.pk, quantity='5', request_key=key, reason='Measured damaged end').pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(wasted, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ContainerStockEvent.objects.count(), 1)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3045'))
