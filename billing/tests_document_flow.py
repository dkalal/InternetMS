from decimal import Decimal

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from billing.models import BillingDocument
from billing.receipt_presentation import receipt_amount_summary
from billing.services import BillingService, BillingServiceError, LineItemInput
from inventory.models import CartSerialSelection, DocumentSerialSelection, InventoryBalance, StockMovement, StockUnit
from inventory.services import CartService
from inventory import tests_cart_checkout as fixtures


class DocumentFlowTests(TestCase):
    setUp = fixtures.CartCheckoutTests.setUp

    def quotation(self):
        self.product.tax_eligible = True
        self.product.save(update_fields=['tax_eligible'])
        self.cart.walk_in_name = 'Snapshot buyer'
        self.cart.discount_amount = Decimal('5')
        self.cart.tax_rate = Decimal('10')
        self.cart.save()
        line = self.cart.lines.get()
        line.discount_amount = Decimal('10')
        line.save()
        return CartService.convert(
            organization=self.org, cart_id=self.cart.pk, target='quotation',
            actor=self.user, membership=self.membership,
        )

    def test_discounted_cart_quotation_invoice_receipt_preserves_amounts_and_posts_once(self):
        quotation = self.quotation()
        self.assertEqual((quotation.subtotal, quotation.discount_amount, quotation.tax_amount, quotation.total),
                         (Decimal('190'), Decimal('5'), Decimal('18.50'), Decimal('203.50')))
        self.assertFalse(StockMovement.objects.exists())
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.user, quotation_id=quotation.pk,
        )
        retry = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.user, quotation_id=quotation.pk,
        )
        self.assertEqual(invoice.pk, retry.pk)
        self.assertEqual(invoice.total, quotation.total)
        self.assertEqual(invoice.items.get().discount_amount, Decimal('10'))
        self.assertFalse(StockMovement.objects.exists())
        line = invoice.items.get()
        invoice = BillingService.update_draft_invoice(
            organization=self.org, performed_by=self.user, invoice_id=invoice.pk,
            tax_rate=invoice.tax_rate, status=BillingDocument.Status.ISSUED,
            items=[LineItemInput(
                product_id=line.product_id, quantity=line.quantity, unit_price=line.unit_price,
                discount_amount=line.discount_amount, unit_snapshot=line.unit_snapshot,
                preserve_unit_price=True,
            )],
        )
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org, created_by=self.user, invoice_id=invoice.pk,
            amount_paid=invoice.total, payment_method='cash',
        )
        summary = receipt_amount_summary(receipt=receipt, source=invoice, items=invoice.items.all())
        self.assertEqual(summary['subtotal'], Decimal('200'))
        self.assertEqual(summary['discount'], Decimal('15'))
        self.assertEqual(summary['tax'], Decimal('18.50'))
        self.assertEqual(summary['total'], receipt.total)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).quantity, Decimal('3'))
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_conversion_preserves_agreed_totals_after_catalog_tax_change(self):
        quotation = self.quotation()
        self.product.tax_eligible = False
        self.product.save(update_fields=['tax_eligible'])
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.user, quotation_id=quotation.pk,
        )
        self.assertEqual(invoice.tax_amount, quotation.tax_amount)
        self.assertEqual(invoice.total, quotation.total)
        line = invoice.items.get()
        invoice = BillingService.update_draft_invoice(
            organization=self.org, performed_by=self.user, invoice_id=invoice.pk,
            tax_rate=invoice.tax_rate, status='issued', items=[LineItemInput(
                source_line_id=line.pk, product_id=line.product_id, quantity=line.quantity,
                unit_price=line.unit_price, discount_amount=line.discount_amount,
                unit_snapshot=line.unit_snapshot, preserve_unit_price=True,
            )],
        )
        self.assertEqual(invoice.tax_amount, quotation.tax_amount)
        self.assertEqual(invoice.total, quotation.total)

    def test_invoice_non_pricing_edit_preserves_saved_price_and_discounts(self):
        quotation = self.quotation()
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.user, quotation_id=quotation.pk,
        )
        line = invoice.items.get()
        self.product.selling_price = Decimal('150')
        self.product.save(update_fields=['selling_price'])
        url = reverse('billing:document_edit', args=['invoice', invoice.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="summary-document-discount">5.00')
        response = self.client.post(url, {
            'tax_rate': '10', 'status': 'draft',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-0-id': str(line.pk), 'items-0-product': str(self.product.pk),
            'items-0-package': '', 'items-0-description': 'Reviewed description',
            'items-0-unit_snapshot': line.unit_snapshot, 'items-0-quantity': '2',
            'items-0-unit_price': '100', 'items-0-discount_amount': '10',
            'items-0-discount_reason': '', 'items-0-pricing_mode': line.pricing_mode,
            'items-0-billing_behavior': line.billing_behavior, 'items-0-promotion': '',
        })
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.items.get().unit_price, Decimal('100'))
        self.assertEqual(invoice.total, Decimal('203.50'))


    def test_publishing_serialized_draft_preserves_line_and_serial_identity(self):
        self.product.is_serialized = True
        self.product.save(update_fields=['is_serialized'])
        for index in range(2):
            stock_unit = StockUnit.objects.create(
                tenant=self.org, organization=self.org, product=self.product,
                serial_number=f'FLOW-{index}', unit_cost=50, status=StockUnit.Status.AVAILABLE,
            )
            CartSerialSelection.objects.create(
                tenant=self.org, cart_line=self.cart.lines.get(), stock_unit=stock_unit,
            )
        quotation = self.quotation()
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.user, quotation_id=quotation.pk,
        )
        line = invoice.items.get()
        selections = set(DocumentSerialSelection.objects.filter(billing_line=line).values_list('pk', flat=True))
        self.assertEqual(len(selections), 2)
        BillingService.update_draft_invoice(
            organization=self.org, performed_by=self.user, invoice_id=invoice.pk,
            tax_rate=invoice.tax_rate, status='issued', items=[LineItemInput(
                source_line_id=line.pk, product_id=line.product_id, quantity=line.quantity,
                unit_price=line.unit_price, discount_amount=line.discount_amount,
                unit_snapshot=line.unit_snapshot, preserve_unit_price=True,
            )],
        )
        self.assertEqual(invoice.items.get().pk, line.pk)
        self.assertEqual(set(DocumentSerialSelection.objects.filter(billing_line=line).values_list('pk', flat=True)), selections)
        self.assertFalse(StockMovement.objects.exists())
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org, created_by=self.user, invoice_id=invoice.pk,
            amount_paid=invoice.total, payment_method='cash',
        )
        self.assertEqual(receipt.total, Decimal('203.50'))
        self.assertEqual(StockUnit.objects.filter(status=StockUnit.Status.SOLD).count(), 2)

    def test_draft_edit_rejects_foreign_line_identity_without_mutation(self):
        quotation = self.quotation()
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.user, quotation_id=quotation.pk,
        )
        line = invoice.items.get()
        with self.assertRaisesMessage(BillingServiceError, 'Invalid or repeated invoice line'):
            BillingService.update_draft_invoice(
                organization=self.org, performed_by=self.user, invoice_id=invoice.pk,
                tax_rate=invoice.tax_rate, items=[LineItemInput(
                    source_line_id=quotation.items.get().pk, description='Foreign', unit_price=Decimal('200'),
                )],
            )
        self.assertEqual(invoice.items.get().pk, line.pk)
        self.assertEqual(invoice.items.get().unit_price, line.unit_price)


    def test_ordinary_draft_keeps_submitted_order_when_inserting_new_rows(self):
        quotation = self.quotation()
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.user, quotation_id=quotation.pk,
        )
        line = invoice.items.get()
        BillingService.update_draft_invoice(
            organization=self.org, performed_by=self.user, invoice_id=invoice.pk,
            tax_rate=invoice.tax_rate, items=[
                LineItemInput(description='Inserted', unit_price=Decimal('20')),
                LineItemInput(
                    source_line_id=line.pk, product_id=line.product_id, description='Saved',
                    quantity=line.quantity, unit_price=line.unit_price, discount_amount=line.discount_amount,
                    unit_snapshot=line.unit_snapshot, preserve_unit_price=True,
                ),
            ],
        )
        self.assertEqual(list(invoice.items.order_by('pk').values_list('description', flat=True)), ['Inserted', 'Saved'])


class QuotationConversionConcurrencyTests(TransactionTestCase):
    setUp = fixtures.CartCheckoutTests.setUp
    quotation = DocumentFlowTests.quotation

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_conversion_returns_one_invoice(self):
        quotation = self.quotation()
        barrier = Barrier(2)

        def convert():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return BillingService.create_invoice_from_quotation(
                    organization=self.org, created_by=self.user, quotation_id=quotation.pk,
                ).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: convert(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(BillingDocument.objects.filter(document_type='invoice').count(), 1)
        self.assertFalse(StockMovement.objects.exists())
