"""Actual locked acquisition costs must not create new negative sale snapshots."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from billing.models import BillingDocument
from billing.services import BillingServiceError
from . import tests as fixtures
from .models import DocumentSerialSelection, InventoryBalance, InventorySaleLine, StockMovement, StockUnit, Purchase, PurchaseLine
from .services import InventoryService


class ActualSaleCostGuardTests(TestCase):
    setUp = fixtures.InventoryAcceptanceTests.setUp
    make_product = fixtures.InventoryAcceptanceTests.make_product
    receive = fixtures.InventoryAcceptanceTests.receive
    invoice = fixtures.InventoryAcceptanceTests.invoice
    pay = fixtures.InventoryAcceptanceTests.pay

    def test_selected_expensive_serial_cannot_hide_behind_lower_weighted_average(self):
        product = self.make_product('Serialized router', 'ACTUAL-SERIAL', serialized=True)
        self.receive(product=product, quantity=1, serials='CHEAP')
        expensive = Purchase.objects.create(tenant=self.org, supplier=self.supplier, reference_number='HIGH-COST', purchase_date=date.today(), created_by=self.admin)
        PurchaseLine.objects.create(purchase=expensive, product=product, quantity=1, unit_cost=180, serial_numbers='EXPENSIVE')
        InventoryService.confirm_purchase(organization=self.org, purchase_id=expensive.pk, actor=self.admin)
        # Two valid receipts: weighted average 140, selected serial cost 180,
        # selling price 150. The average alone would permit an actual loss.
        invoice = self.invoice(product=product, quantity=1)
        unit = StockUnit.objects.get(tenant=self.org, serial_number='EXPENSIVE')
        DocumentSerialSelection.objects.create(tenant=self.org, billing_line=invoice.items.get(), stock_unit=unit)
        before = StockMovement.objects.filter(tenant=self.org).count()
        with self.assertRaisesMessage(BillingServiceError, 'stock cost requirements'):
            self.pay(invoice)
        unit.refresh_from_db()
        self.assertEqual(unit.status, StockUnit.Status.AVAILABLE)
        self.assertIsNone(unit.sold_billing_line_id)
        self.assertEqual(InventoryBalance.objects.get(tenant=self.org, product=product).quantity, 2)
        self.assertEqual(StockMovement.objects.filter(tenant=self.org).count(), before)
        self.assertFalse(InventorySaleLine.objects.filter(tenant=self.org).exists())
        self.assertFalse(BillingDocument.objects.filter(tenant=self.org, document_type=BillingDocument.DocumentType.RECEIPT).exists())

    def test_locked_cost_recheck_blocks_stale_precheck_without_partial_payment(self):
        self.receive(quantity=2)
        invoice = self.invoice(quantity=1)
        # Simulate cost changing after the earlier pricing authorization, as a
        # concurrent receipt can do before the stock balance lock is acquired.
        InventoryBalance.objects.filter(tenant=self.org, product=self.product).update(average_cost=200)
        with patch('billing.services.BillingService._validate_product_cost_floors'):
            with self.assertRaisesMessage(BillingServiceError, 'stock cost requirements'):
                self.pay(invoice)
        self.assertEqual(InventoryBalance.objects.get(tenant=self.org, product=self.product).quantity, 2)
        self.assertFalse(InventorySaleLine.objects.filter(tenant=self.org).exists())
        self.assertFalse(BillingDocument.objects.filter(tenant=self.org, document_type=BillingDocument.DocumentType.RECEIPT).exists())
