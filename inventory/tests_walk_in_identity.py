from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.template.loader import render_to_string

from billing.models import BillingDocument
from billing.forms import BillingDocumentForm
from billing.services import BillingService
from billing.views import _receipt_print_context
from customers.models import Customer
from products.models import Product
from users.models import Organization

from .models import Cart, CartLine
from .forms import CartForm
from .services import CartService
from billing.services import BillingServiceError


class WalkInIdentityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Walk-in shop', slug='walk-in-shop')
        self.actor = get_user_model().objects.create_user(username='walk-in-cashier', password='pass')
        self.product = Product.objects.create(
            organization=self.org, tenant=self.org, name='Router setup', sku='SETUP-1',
            item_type=Product.ItemType.SERVICE, track_stock=False,
            quantity=Decimal('0'), stock=0,
            buying_price=Decimal('10'), selling_price=Decimal('100'),
        )

    def sale(self, name, target=BillingDocument.DocumentType.INVOICE, shipping_address=''):
        cart = Cart.objects.create(
            organization=self.org, tenant=self.org, created_by=self.actor, walk_in_name=name,
            shipping_address=shipping_address,
        )
        CartLine.objects.create(cart=cart, product=self.product, quantity=1, unit_price=Decimal('100'))
        return CartService.convert(organization=self.org, cart_id=cart.pk, target=target, actor=self.actor)

    def test_walk_in_invoices_keep_their_names_and_never_inherit_other_debts(self):
        first = self.sale('Asha')
        second = self.sale('Asha')
        self.assertTrue(first.is_walk_in_sale)
        self.assertEqual(first.display_customer_name, 'Asha')
        self.assertEqual(second.balance_brought_forward, Decimal('0.00'))
        self.assertEqual(BillingService.customer_open_invoice_balance(
            organization=self.org, customer=first.customer,
        ), Decimal('0.00'))
        first.customer.name = 'Changed shared record'
        first.customer.save(update_fields=['name'])
        first.refresh_from_db()
        self.assertEqual(first.display_customer_name, 'Asha')

    def test_walk_in_quotation_invoice_and_reissue_copy_identity(self):
        quote = self.sale('Neema', target=BillingDocument.DocumentType.QUOTATION)
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.actor, quotation_id=quote.pk,
        )
        self.assertTrue(invoice.is_walk_in_sale)
        self.assertEqual(invoice.display_customer_name, 'Neema')
        self.assertEqual(invoice.balance_brought_forward, Decimal('0.00'))
        revision_form = BillingDocumentForm(
            organization=self.org, doc_type=BillingDocument.DocumentType.QUOTATION,
            walk_in_account_id=quote.customer_id, walk_in_name=quote.walk_in_name_snapshot,
        )
        self.assertTrue(revision_form.fields['customer'].queryset.filter(pk=quote.customer_id).exists())
        # Issued cart invoices have the same reissue path as manual invoices.
        issued = self.sale('Juma')
        reissued = BillingService.reissue_invoice(
            organization=self.org, performed_by=self.actor, invoice_id=issued.pk,
            reason='Correcting invoice',
        )
        self.assertTrue(reissued.is_walk_in_sale)
        self.assertEqual(reissued.display_customer_name, 'Juma')

    def test_receipt_keeps_sale_name_and_has_no_customer_account_id(self):
        invoice = self.sale('Rehema')
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org, created_by=self.actor, invoice_id=invoice.pk,
            amount_paid=invoice.total, payment_method='cash',
        )
        self.assertTrue(receipt.is_walk_in_sale)
        self.assertEqual(receipt.display_customer_name, 'Rehema')
        receipt.customer.name = 'Internal account changed'
        receipt.customer.save(update_fields=['name'])
        context = _receipt_print_context(document=receipt, organization=self.org)
        self.assertEqual(context['customer_name'], 'Rehema')
        self.assertEqual(context['customer_account_id'], '')

    def test_registered_customer_with_same_name_remains_a_separate_account(self):
        registered = Customer.objects.create(
            organization=self.org, tenant=self.org, name='Asha',
            customer_type='random', location='Moshi',
        )
        walk_in = self.sale('Asha')
        self.assertFalse(walk_in.customer_id == registered.pk)
        self.assertFalse(Customer.objects.filter(pk=walk_in.customer_id).exists())
        self.assertEqual(BillingService.customer_open_invoice_balance(
            organization=self.org, customer=registered,
        ), Decimal('0.00'))

    def test_walk_in_sale_does_not_mix_tenants(self):
        other = Organization.objects.create(name='Second walk-in shop', slug='second-walk-in-shop')
        account = CartService._walk_in_customer(organization=other)
        invoice = self.sale('Asha')
        self.assertNotEqual(account.pk, invoice.customer_id)
        self.assertEqual(BillingService.customer_open_invoice_balance(
            organization=other, customer=account,
        ), Decimal('0.00'))

    def test_address_is_frozen_per_sale_and_visible_on_print(self):
        first = self.sale('Asha', shipping_address='Moshi, Shop 12')
        second = self.sale('Asha', shipping_address='Arusha, Shop 3')
        collection = self.sale('Asha')
        self.assertEqual(first.shipping_address_snapshot, 'Moshi, Shop 12')
        self.assertEqual(second.shipping_address_snapshot, 'Arusha, Shop 3')
        self.assertEqual(collection.shipping_address_snapshot, '')
        self.assertEqual(first.customer_id, second.customer_id)
        self.assertIn('Ship to', render_to_string('billing/includes/print_overview.html', {'document': first}))
        self.assertIn('Moshi, Shop 12', render_to_string('billing/includes/print_overview.html', {'document': first}))
        self.assertNotIn('Arusha, Shop 3', render_to_string('billing/includes/print_overview.html', {'document': first}))

    def test_address_follows_quotation_invoice_reissue_and_receipt(self):
        quote = self.sale('Neema', target=BillingDocument.DocumentType.QUOTATION, shipping_address='Sokoni 4')
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.actor, quotation_id=quote.pk,
        )
        issued = self.sale('Juma', shipping_address='Kijiji 8')
        reissued = BillingService.reissue_invoice(
            organization=self.org, performed_by=self.actor, invoice_id=issued.pk,
            reason='Correcting invoice',
        )
        paid_invoice = self.sale('Rehema', shipping_address='Kilimanjaro 2')
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org, created_by=self.actor, invoice_id=paid_invoice.pk,
            amount_paid=paid_invoice.total, payment_method='cash',
        )
        self.assertEqual(quote.shipping_address_snapshot, 'Sokoni 4')
        self.assertEqual(invoice.shipping_address_snapshot, 'Sokoni 4')
        self.assertEqual(receipt.shipping_address_snapshot, 'Kilimanjaro 2')
        self.assertEqual(reissued.shipping_address_snapshot, 'Kijiji 8')

    def test_registered_customer_cannot_acquire_walk_in_delivery_address(self):
        registered = Customer.objects.create(
            organization=self.org, tenant=self.org, name='Asha',
            customer_type='random', location='Moshi',
        )
        cart = Cart.objects.create(organization=self.org, tenant=self.org, created_by=self.actor)
        form = CartForm(
            {'customer': registered.pk, 'shipping_address': 'Another home', 'walk_in_name': ''},
            instance=cart, organization=self.org,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('shipping_address', form.errors)
        with self.assertRaises(BillingServiceError):
            BillingService.create_document(
                organization=self.org, created_by=self.actor,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=registered.pk, shipping_address='Another home',
            )
        self.assertFalse(BillingDocument.objects.filter(customer=registered).exists())

    def test_service_rejects_oversized_walk_in_address_without_creating_document(self):
        account = CartService._walk_in_customer(organization=self.org)
        with self.assertRaises(BillingServiceError):
            BillingService.create_document(
                organization=self.org, created_by=self.actor,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=account.pk, walk_in_name='Asha', shipping_address='x' * 501,
            )
        self.assertFalse(BillingDocument.objects.filter(customer=account).exists())
