from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from products.models import Product
from . import tests_purchase_mixed_import as fixtures
from .models import Purchase, PurchaseLine, InventoryBalance, StockMovement, PurchaseProductProposal
from .services import InventoryService, InventoryError


class PurchaseProposalTests(TestCase):
    rows = fixtures.MixedPurchaseImportTests.rows
    payload = fixtures.MixedPurchaseImportTests.payload

    def setUp(self):
        fixtures.MixedPurchaseImportTests.setUp(self)

    def save_draft(self):
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(self.rows()))
        self.assertEqual(response.status_code, 302)
        return Purchase.objects.get()

    def test_draft_is_outside_catalog_and_receipt_registers_once(self):
        purchase = self.save_draft()
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(purchase.product_proposals.count(), 1)
        self.assertFalse(InventoryBalance.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        detail = self.client.get(reverse('inventory:purchase_detail', args=[purchase.pk]))
        self.assertContains(detail, 'Proposed')
        self.assertEqual(detail.context['line_items_total'], Decimal('34.69'))
        received = InventoryService.confirm_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        self.assertEqual(received.status, Purchase.Status.CONFIRMED)
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(Product.objects.get(tenant=self.tenant, name='New radio').sku, 'PRD-000001')
        self.assertEqual(purchase.lines.count(), 2)
        self.assertFalse(purchase.product_proposals.exists())
        self.assertEqual(StockMovement.objects.count(), 2)
        InventoryService.confirm_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_late_receipt_failure_rolls_back_products_lines_and_stock(self):
        purchase = self.save_draft()
        with patch('inventory.services.StockMovement.objects.create', side_effect=ValidationError('forced failure')):
            with self.assertRaises(ValidationError):
                InventoryService.confirm_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(purchase.lines.count(), 1)
        self.assertTrue(purchase.product_proposals.exists())
        self.assertFalse(InventoryBalance.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        from products.models import ProductSKUCounter
        self.assertFalse(ProductSKUCounter.objects.filter(tenant=self.tenant).exists())

    def test_catalog_conflict_preserves_proposal(self):
        purchase = self.save_draft()
        Product.objects.create(organization=self.tenant, tenant=self.tenant, name='New radio',
            sku='CONFLICT', quantity=0, buying_price=0, selling_price=30,
            catalog_category=self.category, sales_unit=self.unit)
        with self.assertRaises(InventoryError):
            InventoryService.confirm_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        self.assertTrue(purchase.product_proposals.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_edit_hydrates_proposals_without_reappending_existing_lines(self):
        purchase = self.save_draft()
        url = reverse('inventory:purchase_edit', args=[purchase.pk])
        response = self.client.get(url)
        self.assertContains(response, 'New radio')
        data = self.payload(purchase.product_proposals.get().rows)
        line = purchase.lines.get()
        data.update({'lines-INITIAL_FORMS': '1', 'lines-0-id': str(line.pk),
                     'lines-0-product': str(self.product.pk), 'lines-0-quantity': '2',
                     'lines-0-unit_cost': '12.345678'})
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(purchase.lines.count(), 1)
        self.assertEqual(purchase.product_proposals.count(), 1)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(purchase.product_proposals.count(), 1)

    def test_cancelled_proposals_never_materialize(self):
        purchase = self.save_draft()
        InventoryService.cancel_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        with self.assertRaises(InventoryError):
            InventoryService.confirm_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        self.assertEqual(Product.objects.count(), 1)
        self.assertFalse(StockMovement.objects.exists())

    def test_legacy_repair_is_explicit_and_only_restages_unused_imports(self):
        from io import StringIO
        from django.core.management import call_command
        from audit.models import AuditLog
        purchase = Purchase.objects.create(organization=self.tenant, tenant=self.tenant,
            supplier=self.supplier, reference_number='OLD-DRAFT', purchase_date='2026-10-21', created_by=self.user)
        old = Product.objects.create(organization=self.tenant, tenant=self.tenant, name='Legacy proposal',
            sku='LEGACY-PROPOSAL', quantity=0, stock=0, buying_price=0, selling_price=30,
            catalog_category=self.category, sales_unit=self.unit)
        PurchaseLine.objects.create(purchase=purchase, tenant=self.tenant, product=old, quantity=2, unit_cost=10)
        AuditLog.objects.create(organization=self.tenant, tenant=self.tenant, actor=self.user,
            action='inventory.product.created_purchase_import', object_type='Product', object_id=str(old.pk))
        options = dict(tenant_id=self.tenant.pk, purchase_id=purchase.pk, stdout=StringIO())
        call_command('stage_purchase_proposals', **options)
        self.assertTrue(Product.objects.filter(pk=old.pk).exists())
        call_command('stage_purchase_proposals', apply=True, **options)
        self.assertFalse(Product.objects.filter(pk=old.pk).exists())
        self.assertEqual(purchase.product_proposals.get().rows[0]['sku'], 'LEGACY-PROPOSAL')
        InventoryService.confirm_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        self.assertEqual(Product.objects.get(sku='LEGACY-PROPOSAL').inventory_balance.quantity, Decimal('2'))

    def test_receiving_requires_product_permission_and_preserves_input_on_denial(self):
        from users.models import TenantMembership
        purchase = self.save_draft()
        TenantMembership.objects.filter(user=self.user, tenant=self.tenant).update(base_role=TenantMembership.BaseRole.SALES)
        with self.assertRaises(InventoryError):
            InventoryService.confirm_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        self.assertEqual(Product.objects.count(), 1)
        self.assertTrue(purchase.product_proposals.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_shared_definition_creates_one_product_with_separate_cost_lines(self):
        new = dict(self.rows()[1], serialized='No', serial_numbers='', sku='000999')
        second = dict(new, unit_cost='12', batch_reference='B2')
        self.assertEqual(self.client.post(reverse('inventory:purchase_create'),
            self.payload([new, second])).status_code, 302)
        purchase = Purchase.objects.get()
        self.assertEqual(Product.objects.count(), 1)
        InventoryService.confirm_purchase(organization=self.tenant, purchase_id=purchase.pk, actor=self.user)
        product = Product.objects.get(sku='000999')
        self.assertEqual(purchase.lines.filter(product=product).count(), 2)
        self.assertEqual(product.inventory_balance.quantity, Decimal('2'))

    def test_legacy_repair_retains_products_with_stock_records(self):
        from io import StringIO
        from django.core.management import call_command
        from audit.models import AuditLog
        purchase = Purchase.objects.create(organization=self.tenant, tenant=self.tenant,
            supplier=self.supplier, reference_number='KEEP-DRAFT', purchase_date='2026-10-21', created_by=self.user)
        PurchaseLine.objects.create(purchase=purchase, tenant=self.tenant, product=self.product, quantity=2, unit_cost=10)
        AuditLog.objects.create(organization=self.tenant, tenant=self.tenant, actor=self.user,
            action='inventory.product.created_purchase_import', object_type='Product', object_id=str(self.product.pk))
        InventoryBalance.objects.create(organization=self.tenant, tenant=self.tenant, product=self.product, quantity=1)
        call_command('stage_purchase_proposals', tenant_id=self.tenant.pk,
            purchase_id=purchase.pk, apply=True, stdout=StringIO())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertFalse(purchase.product_proposals.exists())
