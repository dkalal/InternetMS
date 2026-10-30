from decimal import Decimal
from datetime import date
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from customers.models import Customer
from products.models import Product
from users.models import Organization, TenantMembership
from .models import BillingDocument, BillingLineItem
from .services import BillingService, LineItemInput


class ReceiptTotalsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Receipt totals', slug='receipt-totals')
        self.user = get_user_model().objects.create_user(username='receipt-admin', password='pass')
        TenantMembership.objects.create(tenant=self.org, user=self.user, base_role=TenantMembership.BaseRole.ADMIN_MANAGER)
        self.customer = Customer.objects.create(tenant=self.org, organization=self.org, name='Receipt buyer', customer_type='random')
        self.client.force_login(self.user)

    def sale(self, *, item_discount='0', discount='3', tax='0', quantity='1', price='10', amount=None, items=None):
        invoice = BillingService.create_document(
            organization=self.org, created_by=self.user, document_type='invoice',
            customer_id=self.customer.pk, status='issued', tax_rate=Decimal(tax),
            discount_amount=Decimal(discount),
            items=items or [LineItemInput(
                description='Installation', unit_price=Decimal(price), quantity=Decimal(quantity),
                discount_amount=Decimal(item_discount), unit_snapshot='Job',
            )],
        )
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org, created_by=self.user, invoice_id=invoice.pk,
            amount_paid=Decimal(amount) if amount else invoice.total, payment_method='cash',
        )
        return invoice, receipt

    def print_url(self, receipt):
        return reverse('billing:receipt_print', args=[receipt.pk])

    def pdf_url(self, receipt):
        return reverse('billing:document_pdf', args=['receipt', receipt.pk])

    def assert_totals(self, response, subtotal, discount, tax, total, received):
        summary = response.context['receipt_amounts']
        self.assertEqual(
            [summary[key] for key in ('subtotal', 'discount', 'tax', 'total', 'amount_received')],
            [Decimal(value) for value in (subtotal, discount, tax, total, received)],
        )
        for label in ('Subtotal (before discounts)', 'Discount', 'VAT (', 'AMOUNT RECEIVED'):
            self.assertContains(response, label)

    def test_ten_less_three_shows_full_breakdown_without_changing_history(self):
        invoice, receipt = self.sale()
        before = list(BillingDocument.objects.values())
        response = self.client.get(self.print_url(receipt))
        self.assert_totals(response, '10', '3', '0', '7', '7')
        self.assertContains(response, '<tr><td class="label">Discount</td><td class="value">-3</td></tr>', html=True)
        self.assertContains(response, 'Unit price: 10')
        self.assertContains(response, 'VAT (0%)')
        self.assertEqual(list(BillingDocument.objects.values()), before)
        receipt.refresh_from_db()
        self.assertEqual(receipt.subtotal, Decimal('7'))
        self.assertEqual(receipt.discount_amount, Decimal('0'))

    def test_item_and_sale_discounts_are_counted_once_with_vat(self):
        _, receipt = self.sale(quantity='2', item_discount='3', discount='2', tax='18')
        response = self.client.get(self.print_url(receipt))
        self.assert_totals(response, '20', '5', '2.70', '17.70', '17.70')
        self.assertContains(response, 'Item discount: -3')
        self.assertContains(response, 'VAT (18%)')

    def test_zero_discounts_and_zero_vat_are_still_visible(self):
        _, receipt = self.sale(discount='0')
        response = self.client.get(self.print_url(receipt))
        self.assert_totals(response, '10', '0', '0', '10', '10')
        self.assertNotContains(response, 'Item discount:')
        self.assertContains(response, '<tr><td class="label">Discount</td><td class="value">0</td></tr>', html=True)

    def test_installments_show_invoice_total_separately_from_each_payment(self):
        invoice, receipt = self.sale(discount='0', price='100', tax='18', amount='40')
        response = self.client.get(self.print_url(receipt))
        self.assert_totals(response, '100', '0', '18', '118', '40')
        self.assertContains(response, 'INVOICE TOTAL')
        self.assertContains(response, 'only the payment received below')
        BillingService.create_receipt_from_invoice(
            organization=self.org, created_by=self.user, invoice_id=invoice.pk,
            amount_paid=Decimal('78'), payment_method='cash',
        )
        reprint = self.client.get(self.print_url(receipt))
        self.assertEqual(response.content, reprint.content)

    def test_standalone_historical_receipt_uses_its_own_snapshots(self):
        # Preservation-only legacy shape; new receipt services require invoices.
        receipt = BillingDocument.objects.create(
            organization=self.org, tenant=self.org, created_by=self.user, document_type='receipt',
            number='LEGACY-RCT', issue_date=date(2026, 1, 1), customer=self.customer,
            status='paid', subtotal=Decimal('10'), discount_amount=Decimal('3'),
            tax_rate=0, tax_amount=0, total=Decimal('7'),
        )
        BillingLineItem.objects.create(
            document=receipt, tenant=self.org, organization=self.org,
            description='Historical payment', quantity=1, unit_price=10, line_total=10,
        )
        self.assert_totals(self.client.get(self.print_url(receipt)), '10', '3', '0', '7', '7')

    def test_catalog_changes_cannot_recalculate_historical_tax(self):
        product = Product.objects.create(
            tenant=self.org, organization=self.org, name='Service', sku='RECEIPT-SVC',
            item_type='service', track_stock=False, quantity=0, buying_price=1,
            selling_price=10, tax_eligible=True,
        )
        _, receipt = self.sale(discount='0', tax='18', items=[LineItemInput(
            product_id=product.pk, unit_price=Decimal('10'), description='Saved service',
        )])
        product.tax_eligible = False
        product.selling_price = 200
        product.save(update_fields=['tax_eligible', 'selling_price'])
        self.assert_totals(self.client.get(self.print_url(receipt)), '10', '0', '1.80', '11.80', '11.80')

    def test_real_pdf_contains_same_sale_and_payment_amounts(self):
        _, receipt = self.sale(quantity='2', item_discount='3', discount='2', tax='18')
        response = self.client.get(self.pdf_url(receipt))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertIn('attachment;', response['Content-Disposition'])
        text = ' '.join(' '.join(page.extract_text().split()) for page in PdfReader(BytesIO(response.content)).pages)
        for expected in ('Subtotal (before discounts) 20', 'Discount -5', 'VAT (18%) 2.7', 'TOTAL TZS 17.7', 'AMOUNT RECEIVED TZS 17.7'):
            self.assertIn(expected, text)

    @patch('billing.pdf._load_weasyprint', return_value=None)
    def test_html_fallback_has_same_breakdown(self, _loader):
        _, receipt = self.sale()
        response = self.client.get(self.pdf_url(receipt))
        self.assertEqual(response['X-PDF-Fallback'], 'html')
        self.assert_totals(response, '10', '3', '0', '7', '7')

    def test_both_routes_deny_foreign_tenant_and_foreign_owner(self):
        _, receipt = self.sale()
        outsider = get_user_model().objects.create_user(username='receipt-outsider')
        other_org = Organization.objects.create(name='Other', slug='receipt-other')
        membership = TenantMembership.objects.create(
            tenant=other_org, user=outsider, base_role=TenantMembership.BaseRole.ADMIN_MANAGER,
        )
        self.client.force_login(outsider)
        for url in (self.print_url(receipt), self.pdf_url(receipt)):
            self.assertEqual(self.client.get(url).status_code, 404)
        membership.delete()
        TenantMembership.objects.create(tenant=self.org, user=outsider, base_role=TenantMembership.BaseRole.SALES)
        self.client.logout()
        self.client.force_login(outsider)
        for url in (self.print_url(receipt), self.pdf_url(receipt)):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_corrupt_foreign_invoice_link_fails_closed(self):
        _, receipt = self.sale()
        other_org = Organization.objects.create(name='Other source', slug='receipt-other-source')
        other_customer = Customer.objects.create(tenant=other_org, organization=other_org, name='Other source customer', customer_type='random')
        other_invoice = BillingService.create_document(
            organization=other_org, created_by=self.user, customer_id=other_customer.pk,
            document_type='invoice', status='issued', items=[LineItemInput(description='PRIVATE OTHER SALE', unit_price=Decimal('999'))],
        )
        BillingDocument.objects.filter(pk=receipt.pk).update(invoice=other_invoice)
        for url in (self.print_url(receipt), self.pdf_url(receipt)):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(b'PRIVATE OTHER SALE', response.content)

    def test_untrusted_item_description_is_escaped(self):
        _, receipt = self.sale(items=[LineItemInput(description='<script>alert(1)</script>', unit_price=Decimal('10'))])
        response = self.client.get(self.print_url(receipt))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
