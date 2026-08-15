from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from threading import Barrier, Lock, Thread

from django.contrib.staticfiles import finders
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.middleware.csrf import get_token
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from openpyxl import Workbook
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from audit.models import AuditLog
from billing.models import BillingDocument, BillingLineItem
from billing.services import BillingService, BillingServiceError, LineItemInput
from customers.models import Customer
from products.models import Product, ProductCategory
from services.models import Package
from integrations.models import IntegrationConsumer
from users.models import Organization, UserAccessProfile

from .imports import commit_import, validate_workbook
from .models import (
    Cart,
    CartLine,
    CartSerialSelection,
    DocumentSerialSelection,
    InventoryBalance,
    InventorySale,
    Purchase,
    PurchaseLine,
    StockAdjustment,
    StockMovement,
    StockUnit,
    Supplier,
)
from .numbering import PurchaseReferenceNumberService
from .services import CartService, InventoryError, InventoryService


User = get_user_model()


class InventoryAcceptanceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Tenant A', slug='tenant-a-inventory')
        self.other_org = Organization.objects.create(name='Tenant B', slug='tenant-b-inventory')
        self.admin = User.objects.create_user(username='inventory-admin', password='pass')
        UserAccessProfile.objects.create(user=self.admin, tenant=self.org, role=UserAccessProfile.Role.TENANT_ADMIN)
        self.staff = User.objects.create_user(username='inventory-sales', password='pass')
        UserAccessProfile.objects.create(user=self.staff, tenant=self.org, role=UserAccessProfile.Role.TENANT_STAFF)
        self.customer = Customer.objects.create(
            organization=self.org, tenant=self.org, name='Customer A', customer_type='random', location='Moshi'
        )
        self.product = self.make_product('Router', 'RTR-001')
        self.supplier = Supplier.objects.create(
            organization=self.org, tenant=self.org, company_name='Network Supply Ltd', created_by=self.admin
        )

    def make_product(self, name, sku, *, organization=None, item_type=Product.ItemType.PHYSICAL, serialized=False):
        organization = organization or self.org
        return Product.objects.create(
            organization=organization,
            tenant=organization,
            sku=sku,
            name=name,
            item_type=item_type,
            track_stock=item_type == Product.ItemType.PHYSICAL,
            is_serialized=serialized,
            category='hardware',
            quantity=Decimal('0.00'),
            stock=0,
            measure_unit='Unit',
            buying_price=Decimal('100.00'),
            selling_price=Decimal('150.00'),
            retail_price=Decimal('150.00'),
            reorder_threshold=Decimal('2.00'),
        )

    def receive(self, product=None, quantity=10, *, serials=''):
        product = product or self.product
        purchase = Purchase.objects.create(
            organization=self.org,
            tenant=self.org,
            supplier=self.supplier,
            reference_number=f'PUR-{Purchase.objects.unscoped().count() + 1}',
            purchase_date=date.today(),
            created_by=self.admin,
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            product=product,
            quantity=Decimal(str(quantity)),
            unit_cost=Decimal('100.00'),
            serial_numbers=serials,
        )
        return InventoryService.confirm_purchase(organization=self.org, purchase_id=purchase.pk, actor=self.admin)

    def invoice(self, product=None, quantity=2, *, status=BillingDocument.Status.ISSUED):
        product = product or self.product
        return BillingService.create_document(
            organization=self.org,
            created_by=self.admin,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk,
            status=status,
            tax_rate=Decimal('0.00'),
            items=[LineItemInput(product_id=product.pk, quantity=Decimal(str(quantity)))],
        )

    def pay(self, invoice, *, amount=None, reference='payment-1'):
        return BillingService.create_receipt_from_invoice(
            organization=self.org,
            created_by=self.admin,
            invoice_id=invoice.pk,
            amount_paid=amount if amount is not None else invoice.total,
            payment_method='cash',
            payment_reference=reference,
        )

    def test_01_receiving_ten_units_increases_available_stock(self):
        self.receive(quantity=10)
        balance = InventoryBalance.objects.get(product=self.product)
        self.assertEqual(balance.quantity, Decimal('10.00'))
        self.assertEqual(StockMovement.objects.get(product=self.product).quantity, Decimal('10.00'))

    def test_02_full_payment_reduces_stock_from_ten_to_eight(self):
        self.receive(quantity=10)
        invoice = self.invoice(quantity=2)
        self.pay(invoice)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('8.00'))

    def test_03_04_cart_add_and_draft_save_do_not_change_stock(self):
        self.receive(quantity=10)
        cart = Cart.objects.create(organization=self.org, tenant=self.org, customer=self.customer, created_by=self.admin)
        CartLine.objects.create(cart=cart, product=self.product, quantity=2, unit_price=self.product.selling_price)
        cart.notes = 'Saved for later'
        cart.save()
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('10.00'))
        self.assertFalse(StockMovement.objects.filter(product=self.product, movement_type='sale_out').exists())

    def test_05_quotation_creation_and_approval_do_not_change_stock(self):
        self.receive(quantity=10)
        cart = Cart.objects.create(organization=self.org, tenant=self.org, customer=self.customer, created_by=self.admin)
        CartLine.objects.create(cart=cart, product=self.product, quantity=2, unit_price=self.product.selling_price)
        quote = CartService.convert(
            organization=self.org, cart_id=cart.pk, target=BillingDocument.DocumentType.QUOTATION, actor=self.admin
        )
        BillingService.transition_quotation_status(
            organization=self.org, performed_by=self.admin, quotation_id=quote.pk, to_status=BillingDocument.Status.SENT
        )
        BillingService.transition_quotation_status(
            organization=self.org, performed_by=self.admin, quotation_id=quote.pk, to_status=BillingDocument.Status.ACCEPTED
        )
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('10.00'))

    def test_06_service_item_sells_without_stock_change(self):
        service = self.make_product('Installation', 'SVC-001', item_type=Product.ItemType.SERVICE)
        invoice = self.invoice(product=service, quantity=1)
        self.pay(invoice)
        self.assertFalse(InventoryBalance.objects.filter(product=service).exists())
        self.assertFalse(StockMovement.objects.filter(product=service).exists())
        self.assertTrue(InventorySale.objects.get(invoice=invoice).stock_deducted)

    def test_07_inventory_invoice_rejects_partial_payment(self):
        self.receive(quantity=10)
        invoice = self.invoice(quantity=2)
        with self.assertRaisesMessage(BillingServiceError, 'require complete payment'):
            self.pay(invoice, amount=Decimal('1.00'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('10.00'))

    def test_07b_mixed_product_and_package_invoice_still_requires_full_payment(self):
        self.receive(quantity=10)
        package = Package.objects.create(
            organization=self.org,
            tenant=self.org,
            name='Business Internet',
            package_type='indoor',
            speed='20 Mbps',
            monthly_fee=Decimal('100000.00'),
            setup_fee=Decimal('0.00'),
            description='Internet subscription',
            is_active=True,
        )
        invoice = BillingService.create_document(
            organization=self.org,
            created_by=self.admin,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk,
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal('0.00'),
            items=[
                LineItemInput(product_id=self.product.pk, quantity=Decimal('1.00')),
                LineItemInput(package_id=package.pk, quantity=Decimal('1.00')),
            ],
        )

        with self.assertRaisesMessage(BillingServiceError, 'require complete payment'):
            self.pay(invoice, amount=Decimal('50000.00'), reference='mixed-partial')

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.ISSUED)
        self.assertFalse(invoice.receipts.exists())
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('10.00'))

    def test_08_draft_unpaid_and_void_invoice_do_not_deduct_stock(self):
        self.receive(quantity=10)
        draft = self.invoice(quantity=2, status=BillingDocument.Status.DRAFT)
        unpaid = self.invoice(quantity=2, status=BillingDocument.Status.ISSUED)
        BillingService.void_invoice(organization=self.org, performed_by=self.admin, invoice_id=unpaid.pk, reason='Customer cancelled before payment.')
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('10.00'))
        self.assertFalse(StockMovement.objects.filter(movement_type='sale_out').exists())
        self.assertEqual(draft.status, BillingDocument.Status.DRAFT)

    def test_draft_purchase_can_be_edited_and_cancelled_without_stock_movement(self):
        purchase = Purchase.objects.create(
            organization=self.org, tenant=self.org, supplier=self.supplier,
            reference_number='PUR-CANCEL', purchase_date=date.today(), created_by=self.admin,
        )
        PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=Decimal('2.00'), unit_cost=Decimal('100.00'),
        )
        self.client.login(username='inventory-admin', password='pass')
        self.assertEqual(self.client.get(reverse('inventory:purchase_edit', args=[purchase.pk])).status_code, 200)
        response = self.client.post(reverse('inventory:purchase_cancel', args=[purchase.pk]))
        self.assertRedirects(response, reverse('inventory:purchase_detail', args=[purchase.pk]))
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.CANCELLED)
        self.assertFalse(StockMovement.objects.filter(purchase_line__purchase=purchase).exists())
        with self.assertRaisesMessage(InventoryError, 'Only draft purchases can be confirmed'):
            InventoryService.confirm_purchase(organization=self.org, purchase_id=purchase.pk, actor=self.admin)

    def test_purchase_create_reuses_an_uncommitted_reference_preview(self):
        self.client.login(username='inventory-admin', password='pass')

        first = self.client.get(reverse('inventory:purchase_create'))
        second = self.client.get(reverse('inventory:purchase_create'))

        first_reference = first.context['form'].initial['reference_number']
        second_reference = second.context['form'].initial['reference_number']
        self.assertRegex(first_reference, r'^PUR-\d{4}-\d{5}$')
        self.assertEqual(first_reference, second_reference)
        self.assertNotIn('readonly', first.context['form'].fields['reference_number'].widget.attrs)
        self.assertContains(first, 'Generated automatically')

    def test_purchase_create_allocates_the_preview_only_when_the_draft_is_saved(self):
        self.client.login(username='inventory-admin', password='pass')
        response = self.client.get(reverse('inventory:purchase_create'))
        preview = response.context['form'].initial['reference_number']

        response = self.client.post(reverse('inventory:purchase_create'), {
            'supplier': self.supplier.pk,
            'reference_number': preview,
            'auto_generated_reference': preview,
            'purchase_date': date.today().isoformat(),
            'notes': '',
            'lines-TOTAL_FORMS': 1,
            'lines-INITIAL_FORMS': 0,
            'lines-MIN_NUM_FORMS': 0,
            'lines-MAX_NUM_FORMS': 1000,
            'lines-0-product': self.product.pk,
            'lines-0-quantity': '1.00',
            'lines-0-unit_cost': '100.00',
            'lines-0-batch_reference': '',
            'lines-0-expiry_date': '',
            'lines-0-serial_numbers': '',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Purchase.objects.get().reference_number, preview)
        self.assertEqual(
            PurchaseReferenceNumberService.preview_next_number(organization=self.org),
            f'PUR-{date.today():%Y}-00002',
        )

    def test_purchase_create_preserves_an_edited_supplier_reference(self):
        self.client.login(username='inventory-admin', password='pass')
        preview = self.client.get(reverse('inventory:purchase_create')).context['form'].initial['reference_number']

        response = self.client.post(reverse('inventory:purchase_create'), {
            'supplier': self.supplier.pk,
            'reference_number': 'DELIVERY-INV-2048',
            'auto_generated_reference': preview,
            'purchase_date': date.today().isoformat(),
            'notes': '',
            'lines-TOTAL_FORMS': 1,
            'lines-INITIAL_FORMS': 0,
            'lines-MIN_NUM_FORMS': 0,
            'lines-MAX_NUM_FORMS': 1000,
            'lines-0-product': self.product.pk,
            'lines-0-quantity': '1.00',
            'lines-0-unit_cost': '100.00',
            'lines-0-batch_reference': '',
            'lines-0-expiry_date': '',
            'lines-0-serial_numbers': '',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Purchase.objects.get().reference_number, 'DELIVERY-INV-2048')
        self.assertEqual(
            PurchaseReferenceNumberService.preview_next_number(organization=self.org),
            f'PUR-{date.today():%Y}-00001',
        )

    def test_generated_purchase_reference_skips_existing_manual_reference(self):
        Purchase.objects.create(
            organization=self.org,
            tenant=self.org,
            supplier=self.supplier,
            reference_number=f'PUR-{date.today():%Y}-00001',
            purchase_date=date.today(),
            created_by=self.admin,
        )

        reference = PurchaseReferenceNumberService.next_number(organization=self.org)

        self.assertEqual(reference, f'PUR-{date.today():%Y}-00002')

    def test_draft_purchase_detail_uses_line_total_when_stored_total_is_stale(self):
        purchase = Purchase.objects.create(
            organization=self.org, tenant=self.org, supplier=self.supplier,
            reference_number='PUR-DRAFT-TOTAL', purchase_date=date.today(), created_by=self.admin,
            total_cost=Decimal('0.00'),
        )
        PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=Decimal('10.00'), unit_cost=Decimal('150000.00'),
        )
        self.client.login(username='inventory-admin', password='pass')

        response = self.client.get(reverse('inventory:purchase_detail', args=[purchase.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['line_items_total'], Decimal('1500000.00'))
        self.assertContains(response, '1,500,000.00 TZS')

    def test_cart_invoice_is_issued_and_can_create_a_receipt(self):
        self.receive(quantity=4)
        cart = Cart.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer, created_by=self.admin,
        )
        CartLine.objects.create(cart=cart, product=self.product, quantity=Decimal('2.00'), unit_price=Decimal('150.00'))
        invoice = CartService.convert(
            organization=self.org, cart_id=cart.pk, target=BillingDocument.DocumentType.INVOICE, actor=self.admin,
        )
        self.assertEqual(invoice.status, BillingDocument.Status.ISSUED)
        receipt = self.pay(invoice, reference='cart-checkout-payment')
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.PAID)
        self.assertEqual(receipt.invoice_id, invoice.id)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2.00'))

    def test_pos_quantity_controls_add_and_remove_without_changing_stock(self):
        self.receive(quantity=2)
        cart = Cart.objects.create(organization=self.org, tenant=self.org, created_by=self.admin)
        self.client.login(username='inventory-admin', password='pass')
        url = reverse('inventory:cart_line_adjust', args=[cart.pk])
        self.client.post(url, {'product': self.product.pk, 'direction': 'add'})
        self.client.post(url, {'product': self.product.pk, 'direction': 'increase'})
        line = CartLine.objects.get(cart=cart, product=self.product)
        self.assertEqual(line.quantity, Decimal('2.00'))
        self.client.post(url, {'product': self.product.pk, 'direction': 'decrease'})
        self.assertEqual(CartLine.objects.get(cart=cart, product=self.product).quantity, Decimal('1.00'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2.00'))

    def test_start_sale_opens_an_editable_pos_draft_immediately(self):
        self.client.login(username='inventory-admin', password='pass')
        response = self.client.post(reverse('inventory:cart_create'))
        cart = Cart.objects.get(created_by=self.admin)
        self.assertRedirects(response, reverse('inventory:cart_detail', args=[cart.pk]))
        self.assertEqual(cart.status, Cart.Status.DRAFT)
        repeat_response = self.client.post(reverse('inventory:cart_create'))
        self.assertRedirects(repeat_response, reverse('inventory:cart_detail', args=[cart.pk]))
        self.assertEqual(Cart.objects.filter(created_by=self.admin, status=Cart.Status.DRAFT).count(), 1)
        self.assertEqual(self.client.get(reverse('inventory:cart_create')).status_code, 404)

    def test_discard_sale_is_soft_idempotent_audited_and_never_changes_stock(self):
        self.receive(quantity=5)
        cart = Cart.objects.create(organization=self.org, tenant=self.org, created_by=self.admin)
        CartLine.objects.create(
            cart=cart, product=self.product, quantity=Decimal('2.00'), unit_price=Decimal('150.00')
        )
        self.client.login(username='inventory-admin', password='pass')
        url = reverse('inventory:cart_abandon', args=[cart.pk])

        response = self.client.post(url, follow=True)

        self.assertRedirects(response, reverse('inventory:cart_list'))
        self.assertContains(response, 'Sale discarded')
        cart.refresh_from_db()
        self.assertEqual(cart.status, Cart.Status.ABANDONED)
        self.assertEqual(cart.lines.count(), 1)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('5.00'))
        log = AuditLog.objects.get(action='inventory.cart.abandoned', object_id=str(cart.pk))
        self.assertEqual(log.old_value['status'], Cart.Status.DRAFT)
        self.assertEqual(log.new_value['status'], Cart.Status.ABANDONED)
        self.assertEqual(log.metadata['stock_changed'], False)
        self.assertEqual(log.metadata['financial_document_created'], False)

        repeat = self.client.post(url, follow=True)
        self.assertContains(repeat, 'already discarded')
        self.assertEqual(
            AuditLog.objects.filter(action='inventory.cart.abandoned', object_id=str(cart.pk)).count(),
            1,
        )

    def test_discard_sale_rejects_get_converted_and_cross_tenant_carts(self):
        cart = Cart.objects.create(organization=self.org, tenant=self.org, created_by=self.admin)
        CartLine.objects.create(cart=cart, product=self.product, quantity=1, unit_price=Decimal('150.00'))
        quotation = CartService.convert(
            organization=self.org,
            cart_id=cart.pk,
            target=BillingDocument.DocumentType.QUOTATION,
            actor=self.admin,
        )
        other_cart = Cart.objects.create(
            organization=self.other_org, tenant=self.other_org, created_by=self.admin
        )
        self.client.login(username='inventory-admin', password='pass')

        self.assertEqual(self.client.get(reverse('inventory:cart_abandon', args=[cart.pk])).status_code, 404)
        response = self.client.post(reverse('inventory:cart_abandon', args=[cart.pk]), follow=True)
        self.assertContains(response, 'Only an unconverted draft sale can be discarded')
        cart.refresh_from_db()
        self.assertEqual(cart.status, Cart.Status.CONVERTED)
        self.assertEqual(cart.quotation_id, quotation.pk)
        self.assertEqual(
            self.client.post(reverse('inventory:cart_abandon', args=[other_cart.pk])).status_code,
            404,
        )

    def test_pos_large_customer_and_category_sets_use_searchable_controls_without_category_chips(self):
        Customer.objects.bulk_create([
            Customer(
                organization=self.org,
                tenant=self.org,
                name=f'Customer {index:03d}',
                customer_type='random',
                status=Customer.Status.ACTIVE,
                location='Moshi',
            )
            for index in range(120)
        ])
        ProductCategory.objects.bulk_create([
            ProductCategory(
                organization=self.org,
                tenant=self.org,
                name=f'Category {index:03d}',
            )
            for index in range(120)
        ])
        cart = Cart.objects.create(organization=self.org, tenant=self.org, created_by=self.admin)
        self.client.login(username='inventory-admin', password='pass')

        response = self.client.get(reverse('inventory:cart_detail', args=[cart.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_customer"', html=False)
        self.assertContains(response, 'data-searchable-select="true"', count=2, html=False)
        self.assertContains(response, 'Customer 119')
        self.assertContains(response, 'Category 119')
        self.assertNotContains(response, 'href="?category=', html=False)

    def test_confirmation_dialog_uses_scoped_hooks_that_cannot_match_action_forms(self):
        script_path = finders.find('inventory/js/jims-ui.js')
        self.assertIsNotNone(script_path)
        script = Path(script_path).read_text(encoding='utf-8')
        self.assertIn('confirmLayer.querySelector("[data-confirm-dialog-title]")', script)
        self.assertNotIn('document.querySelector("[data-confirm-title]")', script)

    def test_serialized_cart_line_shows_searchable_available_serials_and_saves_selection(self):
        serialized_product = self.make_product('Managed Router', 'SER-UI-001', serialized=True)
        self.receive(product=serialized_product, quantity=2, serials='SN-UI-001\nSN-UI-002')
        cart = Cart.objects.create(organization=self.org, tenant=self.org, created_by=self.admin)
        self.client.login(username='inventory-admin', password='pass')
        url = reverse('inventory:cart_line_create', args=[cart.pk])

        response = self.client.get(url, {'product': serialized_product.pk})

        self.assertContains(response, 'SN-UI-001')
        self.assertContains(response, 'data-serial-search')
        serial = StockUnit.objects.get(product=serialized_product, serial_number='SN-UI-001')
        response = self.client.post(url, {
            'product': serialized_product.pk,
            'quantity': '1.00',
            'discount_amount': '0.00',
            'serial_units': [serial.pk],
        })
        line = CartLine.objects.get(cart=cart, product=serialized_product)
        self.assertRedirects(response, reverse('inventory:cart_detail', args=[cart.pk]))
        self.assertTrue(CartSerialSelection.objects.filter(cart_line=line, stock_unit=serial).exists())

    def test_serialized_walk_in_cart_uses_wholesale_price_at_product_threshold(self):
        serialized_product = self.make_product('Bulk Router', 'BULK-SER-001', serialized=True)
        serial_numbers = [f'BULK-SN-{number:03d}' for number in range(1, 6)]
        self.receive(product=serialized_product, quantity=5, serials='\n'.join(serial_numbers))
        serialized_product.allow_wholesale = True
        serialized_product.wholesale_price = Decimal('120.00')
        serialized_product.wholesale_min_quantity = Decimal('5.00')
        serialized_product.save(update_fields=['allow_wholesale', 'wholesale_price', 'wholesale_min_quantity'])
        cart = Cart.objects.create(
            organization=self.org,
            tenant=self.org,
            created_by=self.admin,
            sale_pricing_category=Cart.SalePricingCategory.WHOLESALE,
        )
        self.client.login(username='inventory-admin', password='pass')

        response = self.client.post(reverse('inventory:cart_line_create', args=[cart.pk]), {
            'product': serialized_product.pk,
            'quantity': '5.00',
            'discount_amount': '0.00',
            'serial_units': list(StockUnit.objects.filter(product=serialized_product).values_list('pk', flat=True)),
        })

        line = CartLine.objects.get(cart=cart, product=serialized_product)
        self.assertRedirects(response, reverse('inventory:cart_detail', args=[cart.pk]))
        self.assertEqual(line.unit_price, Decimal('120.00'))
        invoice = CartService.convert(
            organization=self.org, cart_id=cart.pk, target=BillingDocument.DocumentType.INVOICE, actor=self.admin,
        )
        self.assertEqual(invoice.items.get().unit_price, Decimal('120.00'))
        self.assertEqual(invoice.items.get().pricing_mode, BillingLineItem.PricingMode.WHOLESALE)

    def test_pos_ajax_reprices_wholesale_customer_at_product_minimum(self):
        self.receive(quantity=5)
        self.customer.pricing_tier = Customer.PricingTier.WHOLESALE
        self.customer.save(update_fields=['pricing_tier'])
        self.product.allow_wholesale = True
        self.product.wholesale_price = Decimal('120.00')
        self.product.wholesale_min_quantity = Decimal('3.00')
        self.product.save(update_fields=['allow_wholesale', 'wholesale_price', 'wholesale_min_quantity'])
        cart = Cart.objects.create(organization=self.org, tenant=self.org, customer=self.customer, created_by=self.admin)
        self.client.login(username='inventory-admin', password='pass')
        url = reverse('inventory:cart_line_adjust', args=[cart.pk])
        headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest', 'HTTP_ACCEPT': 'application/json'}
        for _ in range(2):
            response = self.client.post(url, {'product': self.product.pk, 'direction': 'add'}, **headers)
            self.assertEqual(response.status_code, 200)
        self.assertIn('Continue to payment', response.json()['checkout_html'])
        self.assertIn('300.00', response.json()['checkout_html'])
        line = CartLine.objects.get(cart=cart, product=self.product)
        self.assertEqual(line.unit_price, Decimal('150.00'))
        response = self.client.post(url, {'product': self.product.pk, 'direction': 'increase'}, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['lines'][0]['pricing_mode'], BillingLineItem.PricingMode.WHOLESALE)
        line.refresh_from_db()
        self.assertEqual(line.unit_price, Decimal('120.00'))
        invoice = CartService.convert(
            organization=self.org, cart_id=cart.pk, target=BillingDocument.DocumentType.INVOICE, actor=self.admin,
        )
        self.assertEqual(invoice.items.get().unit_price, Decimal('120.00'))
        self.assertEqual(invoice.items.get().pricing_mode, BillingLineItem.PricingMode.WHOLESALE)

    def test_pos_ajax_saves_sale_details_and_returns_authoritative_checkout_total(self):
        self.product.tax_eligible = False
        self.product.save(update_fields=['tax_eligible'])
        cart = Cart.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer, created_by=self.admin,
        )
        CartLine.objects.create(
            cart=cart, product=self.product, quantity=Decimal('1.00'), unit_price=Decimal('150.00'),
        )
        self.client.login(username='inventory-admin', password='pass')

        response = self.client.post(reverse('inventory:cart_detail', args=[cart.pk]), {
            'customer': self.customer.pk,
            'walk_in_name': '',
            'sale_pricing_category': Cart.SalePricingCategory.STANDARD,
            'discount_amount': '10.00',
            'tax_rate': '18.00',
            'notes': '',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest', HTTP_ACCEPT='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['taxable_subtotal'], '0.00')
        self.assertEqual(response.json()['tax'], '0.00')
        self.assertEqual(response.json()['grand_total'], '140.00')
        self.assertIn('140.00', response.json()['checkout_html'])
        cart.refresh_from_db()
        self.assertEqual(cart.discount_amount, Decimal('10.00'))
        self.assertEqual(cart.tax_rate, Decimal('18.00'))

    def test_pos_ajax_rejects_cart_discount_above_repriced_subtotal_atomically(self):
        cart = Cart.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer, created_by=self.admin,
        )
        CartLine.objects.create(
            cart=cart, product=self.product, quantity=Decimal('1.00'), unit_price=Decimal('150.00'),
        )
        self.client.login(username='inventory-admin', password='pass')

        response = self.client.post(reverse('inventory:cart_detail', args=[cart.pk]), {
            'customer': self.customer.pk,
            'walk_in_name': '',
            'sale_pricing_category': Cart.SalePricingCategory.STANDARD,
            'discount_amount': '151.00',
            'tax_rate': '18.00',
            'notes': '',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest', HTTP_ACCEPT='application/json')

        self.assertEqual(response.status_code, 422)
        self.assertIn('discount_amount', response.json()['errors'])
        cart.refresh_from_db()
        self.assertEqual(cart.discount_amount, Decimal('0.00'))

    def test_09_same_invoice_cannot_deduct_stock_twice(self):
        self.receive(quantity=10)
        invoice = self.invoice(quantity=2)
        first = self.pay(invoice, reference='idempotent-ref')
        second = self.pay(invoice, reference='idempotent-ref')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('8.00'))
        self.assertEqual(StockMovement.objects.filter(product=self.product, movement_type='sale_out').count(), 1)

    def test_10_insufficient_stock_rolls_back_receipt_and_confirmation(self):
        self.receive(quantity=1)
        invoice = self.invoice(quantity=2)
        with self.assertRaisesMessage(BillingServiceError, 'Insufficient stock'):
            self.pay(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.ISSUED)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('1.00'))
        self.assertFalse(BillingDocument.objects.filter(document_type='receipt', invoice=invoice).exists())

    def test_11_12_serial_required_and_sold_serial_cannot_be_reused(self):
        serialized = self.make_product('Managed Router', 'SER-001', serialized=True)
        self.receive(product=serialized, quantity=2, serials='SN-001\nSN-002')
        invoice = self.invoice(product=serialized, quantity=1)
        with self.assertRaisesMessage(BillingServiceError, 'Select exactly 1'):
            self.pay(invoice, reference='serial-missing')
        line = invoice.items.get()
        unit = StockUnit.objects.get(serial_number='SN-001')
        DocumentSerialSelection.objects.create(
            organization=self.org, tenant=self.org, billing_line=line, stock_unit=unit
        )
        self.pay(invoice, reference='serial-sale')
        unit.refresh_from_db()
        self.assertEqual(unit.status, StockUnit.Status.SOLD)
        second_invoice = self.invoice(product=serialized, quantity=1)
        second_line = second_invoice.items.get()
        DocumentSerialSelection.objects.create(
            organization=self.org, tenant=self.org, billing_line=second_line, stock_unit=unit
        )
        with self.assertRaisesMessage(BillingServiceError, 'no longer available'):
            self.pay(second_invoice, reference='serial-resale')

    def test_13_adjustment_requires_reason_and_authorized_view(self):
        self.receive(quantity=2)
        with self.assertRaisesMessage(InventoryError, 'valid adjustment reason'):
            InventoryService.adjust_stock(
                organization=self.org, product_id=self.product.pk, quantity_delta=-1,
                reason='', actor=self.admin,
            )
        self.client.login(username='inventory-sales', password='pass')
        response = self.client.get(reverse('inventory:stock_adjust'))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(StockAdjustment.objects.exists())

    def test_14_sales_user_cannot_access_purchases_costs_or_profit(self):
        self.client.login(username='inventory-sales', password='pass')
        self.assertEqual(self.client.get(reverse('inventory:purchase_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('inventory:stock_adjust')).status_code, 403)
        self.assertEqual(self.client.get(reverse('inventory:report', args=['gross-profit'])).status_code, 403)
        response = self.client.get(reverse('product-detail', args=[self.product.pk]))
        self.assertNotContains(response, 'Buying price')

    def test_15_tenant_isolation_blocks_reads_and_service_mutation(self):
        other_product = self.make_product('Other Router', 'OTHER-001', organization=self.other_org)
        self.client.login(username='inventory-admin', password='pass')
        self.assertEqual(self.client.get(reverse('product-detail', args=[other_product.pk])).status_code, 404)
        other_supplier = Supplier.objects.create(
            organization=self.other_org, tenant=self.other_org, company_name='Other Supplier', created_by=self.admin
        )
        purchase = Purchase.objects.create(
            organization=self.other_org, tenant=self.other_org, supplier=other_supplier,
            reference_number='OTHER-PUR', purchase_date=date.today(), created_by=self.admin,
        )
        PurchaseLine.objects.create(purchase=purchase, product=other_product, quantity=1, unit_cost=1)
        with self.assertRaises(PermissionDenied):
            InventoryService.confirm_purchase(organization=self.org, purchase_id=purchase.pk, actor=self.admin)

    def test_16_invalid_import_reports_rows_and_commits_nothing(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['sku', 'quantity', 'serial_numbers', 'reason_notes'])
        sheet.append([self.product.sku, 3, '', 'valid row'])
        sheet.append(['MISSING-SKU', 4, '', 'invalid row'])
        payload = BytesIO()
        workbook.save(payload)
        payload.seek(0)
        payload.name = 'opening.xlsx'
        job = validate_workbook(
            organization=self.org, actor=self.admin, import_type='opening_stock', uploaded_file=payload
        )
        self.assertGreater(job.error_count, 0)
        with self.assertRaises(ValueError):
            commit_import(organization=self.org, actor=self.admin, job_id=job.pk)
        self.assertFalse(InventoryBalance.objects.filter(product=self.product).exists())

    def test_17_package_invoice_accepts_partial_payment_without_inventory_sale(self):
        package = Package.objects.create(
            organization=self.org, tenant=self.org, name='10 Mbps', package_type='indoor', speed='10 Mbps',
            monthly_fee=Decimal('100.00'), setup_fee=Decimal('0.00'), description='Internet package'
        )
        invoice = BillingService.create_document(
            organization=self.org, created_by=self.admin, document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk, status=BillingDocument.Status.ISSUED, tax_rate=0,
            items=[LineItemInput(package_id=package.pk, quantity=Decimal('1.00'), unit_price=package.monthly_fee)],
        )
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org, created_by=self.admin, invoice_id=invoice.pk, amount_paid=Decimal('50.00'),
            payment_method='cash', payment_reference='package-partial',
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.PARTIALLY_PAID)
        self.assertEqual(receipt.total, Decimal('50.00'))
        self.assertEqual(BillingService.invoice_remaining_balance(organization=self.org, invoice=invoice), Decimal('50.00'))
        self.assertFalse(InventorySale.objects.filter(invoice=invoice).exists())

    def test_cart_calculation_order_and_fixed_price(self):
        self.receive(quantity=10)
        cart = Cart.objects.create(
            organization=self.org, tenant=self.org, customer=self.customer, created_by=self.admin,
            discount_amount=Decimal('30.00'), tax_rate=Decimal('18.00'),
        )
        CartLine.objects.create(
            cart=cart, product=self.product, quantity=Decimal('2.00'),
            unit_price=Decimal('1.00'), discount_amount=Decimal('20.00'),
        )
        invoice = CartService.convert(
            organization=self.org, cart_id=cart.pk,
            target=BillingDocument.DocumentType.INVOICE, actor=self.admin,
        )
        line = invoice.items.get()
        self.assertEqual(line.unit_price, Decimal('150.00'))
        self.assertEqual(invoice.subtotal, Decimal('280.00'))
        self.assertEqual(invoice.discount_amount, Decimal('30.00'))
        self.assertEqual(invoice.tax_amount, Decimal('45.00'))
        self.assertEqual(invoice.total, Decimal('295.00'))
        direct = BillingService.create_document(
            organization=self.org, created_by=self.admin, document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk, status=BillingDocument.Status.DRAFT, tax_rate=0,
            items=[LineItemInput(
                product_id=self.product.pk, quantity=Decimal('1.00'), unit_price=Decimal('1.00'),
                pricing_mode=BillingLineItem.PricingMode.MANUAL,
            )],
        )
        self.assertEqual(direct.items.get().unit_price, Decimal('150.00'))

    def test_cart_conversion_excludes_tax_exempt_product_from_vat(self):
        self.product.tax_eligible = False
        self.product.save(update_fields=['tax_eligible'])
        self.receive(quantity=1)
        cart = Cart.objects.create(
            organization=self.org,
            tenant=self.org,
            customer=self.customer,
            created_by=self.admin,
            tax_rate=Decimal('18.00'),
        )
        CartLine.objects.create(
            cart=cart,
            product=self.product,
            quantity=Decimal('1.00'),
            unit_price=Decimal('150.00'),
        )

        invoice = CartService.convert(
            organization=self.org,
            cart_id=cart.pk,
            target=BillingDocument.DocumentType.INVOICE,
            actor=self.admin,
        )

        self.assertEqual(invoice.tax_amount, Decimal('0.00'))
        self.assertEqual(invoice.total, Decimal('150.00'))

    def test_inventory_pages_and_excel_export_render_for_admin(self):
        self.client.login(username='inventory-admin', password='pass')
        for url in [
            reverse('inventory:dashboard'), reverse('inventory:category_list'), reverse('inventory:supplier_list'),
            reverse('inventory:purchase_list'), reverse('inventory:purchase_create'), reverse('inventory:stock_list'),
            reverse('inventory:movement_list'), reverse('inventory:cart_list'), reverse('inventory:import_data'),
            reverse('inventory:settings'), reverse('inventory:report', args=['stock-valuation']),
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        cart = Cart.objects.create(organization=self.org, tenant=self.org, customer=self.customer, created_by=self.admin)
        detail = self.client.get(reverse('inventory:cart_detail', args=[cart.pk]), {'q': 'Router'})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Point of sale')
        export = self.client.get(reverse('inventory:report', args=['stock-valuation']), {'export': 'xlsx'})
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_category_editor_saves_icon_and_default_unit(self):
        self.client.login(username='inventory-admin', password='pass')
        create_url = reverse('inventory:category_create')

        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Category icon')
        self.assertContains(response, 'Applied automatically to new products')

        response = self.client.post(create_url, {
            'name': 'Cables',
            'description': 'Copper and fiber cabling.',
            'measure_unit': 'Meter',
            'icon': ProductCategory.Icon.CABLE,
            'is_active': 'on',
        })

        self.assertRedirects(response, reverse('inventory:category_list'))
        category = ProductCategory.objects.get(tenant=self.org, name='Cables')
        self.assertEqual(category.measure_unit, 'Meter')
        self.assertEqual(category.icon, ProductCategory.Icon.CABLE)

    def test_movement_list_renders_system_created_movements(self):
        StockMovement.objects.create(
            organization=self.org,
            tenant=self.org,
            product=self.product,
            movement_type=StockMovement.MovementType.OPENING,
            quantity=Decimal('5.00'),
            balance_after=Decimal('5.00'),
            created_by=None,
        )
        self.client.login(username='inventory-admin', password='pass')

        response = self.client.get(reverse('inventory:movement_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'System')

    def test_purchase_confirmation_submits_with_csrf_token(self):
        purchase = Purchase.objects.create(
            organization=self.org,
            tenant=self.org,
            supplier=self.supplier,
            reference_number='PUR-CSRF-1',
            purchase_date=date.today(),
            created_by=self.admin,
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.product,
            quantity=Decimal('2.00'),
            unit_cost=Decimal('100.00'),
        )
        client = Client(enforce_csrf_checks=True)
        client.login(username='inventory-admin', password='pass')

        detail = client.get(reverse('inventory:purchase_detail', args=[purchase.pk]))
        confirmation = client.post(
            reverse('inventory:purchase_confirm', args=[purchase.pk]),
            {'csrfmiddlewaretoken': get_token(detail.wsgi_request)},
        )

        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'name="csrfmiddlewaretoken"')
        self.assertIn('no-cache', detail['Cache-Control'])
        self.assertEqual(confirmation.status_code, 302)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.CONFIRMED)

    def test_inventory_ui_uses_permission_safe_operational_states(self):
        self.receive(quantity=2)
        self.client.login(username='inventory-sales', password='pass')

        dashboard = self.client.get(reverse('inventory:dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Catalog items')
        self.assertContains(dashboard, 'Low stock')
        self.assertNotContains(dashboard, 'Stock value')
        self.assertNotContains(dashboard, 'Total wholesale')

        stock = self.client.get(reverse('inventory:stock_list'), {'state': 'low'})
        self.assertEqual(stock.status_code, 200)
        self.assertContains(stock, 'Low stock')
        self.assertContains(stock, self.product.sku)
        self.assertNotContains(stock, 'Average cost')

    def test_inventory_dashboard_values_current_stock_at_each_sale_price_category(self):
        self.product.technician_price = Decimal('135.00')
        self.product.allow_wholesale = True
        self.product.wholesale_price = Decimal('120.00')
        self.product.wholesale_min_quantity = Decimal('2.00')
        self.product.save(update_fields=[
            'technician_price', 'allow_wholesale', 'wholesale_price', 'wholesale_min_quantity',
        ])
        self.receive(quantity=2)
        self.client.login(username='inventory-admin', password='pass')

        dashboard = self.client.get(reverse('inventory:dashboard'))

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.context['total_stock_value'], Decimal('200.00'))
        self.assertEqual(dashboard.context['total_technician_value'], Decimal('270.00'))
        self.assertEqual(dashboard.context['total_wholesale_value'], Decimal('240.00'))
        self.assertEqual(dashboard.context['total_selling_value'], Decimal('300.00'))
        self.assertContains(dashboard, 'Technician sales value')
        self.assertContains(dashboard, 'Wholesale sales value')
        self.assertContains(dashboard, 'Standard sales value')

    def test_stock_adjustment_ui_previews_balance_and_requires_confirmation(self):
        self.receive(quantity=3)
        self.client.login(username='inventory-admin', password='pass')

        response = self.client.get(reverse('inventory:stock_adjust'), {'product': self.product.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Increase stock')
        self.assertContains(response, 'Decrease stock')
        self.assertContains(response, 'Current stock')
        self.assertContains(response, 'Expected stock')
        self.assertContains(response, 'immutable history record')

    def test_inventory_invoice_payment_ui_explains_full_payment_rule(self):
        self.receive(quantity=2)
        invoice = self.invoice(quantity=1)
        self.client.login(username='inventory-admin', password='pass')

        response = self.client.get(reverse('billing:create_receipt_from_invoice', args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'requires full payment')
        self.assertContains(response, 'Partial payments are not accepted for this inventory invoice')
        self.assertNotContains(response, 'you can register another payment later')


class CartTechnicianPricingTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="POS Pricing", slug="pos-pricing")
        self.user = User.objects.create_user(username="pos-pricing-user", password="pass")
        self.product = Product.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name="Cable tester",
            sku="TESTER-1",
            item_type=Product.ItemType.SERVICE,
            track_stock=False,
            quantity=Decimal("0.00"),
            buying_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            technician_price=Decimal("125.00"),
        )

    def test_cart_category_controls_price_without_registered_technician(self):
        cart = Cart.objects.create(
            organization=self.organization,
            tenant=self.organization,
            created_by=self.user,
            walk_in_name="Independent installer",
            sale_pricing_category=Cart.SalePricingCategory.TECHNICIAN,
        )
        unit_price, mode = CartService.line_pricing(
            product=self.product,
            quantity=Decimal("1.00"),
            sale_pricing_category=cart.sale_pricing_category,
        )
        self.assertEqual(unit_price, Decimal("125.00"))
        self.assertEqual(mode, BillingLineItem.PricingMode.TECHNICIAN)
        self.assertIsNone(cart.customer)

    def test_customer_tier_mode_uses_registered_technician_price(self):
        customer = Customer.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name="Registered installer",
            customer_type="random",
            pricing_tier=Customer.PricingTier.TECHNICIAN,
        )

        unit_price, mode = CartService.line_pricing(
            product=self.product,
            quantity=Decimal("1.00"),
            customer=customer,
            sale_pricing_category=Cart.SalePricingCategory.CUSTOMER_TIER,
        )

        self.assertEqual(unit_price, Decimal("125.00"))
        self.assertEqual(mode, BillingLineItem.PricingMode.TECHNICIAN)


class InventoryAPITests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='API Tenant', slug='api-inventory-tenant')
        self.other_org = Organization.objects.create(name='Other API Tenant', slug='other-api-inventory-tenant')
        self.user = User.objects.create_user(username='inventory-api-admin', password='pass')
        UserAccessProfile.objects.create(user=self.user, tenant=self.org, role=UserAccessProfile.Role.TENANT_ADMIN)
        IntegrationConsumer.objects.create(user=self.user, organization=self.org, name='Inventory API')
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.customer = Customer.objects.create(
            organization=self.org, tenant=self.org, name='API Customer', customer_type='random', location='Moshi'
        )
        self.product = Product.objects.create(
            organization=self.org, tenant=self.org, sku='API-RTR', name='API Router', item_type='physical',
            track_stock=True, quantity=0, stock=0, measure_unit='Unit', buying_price=100, selling_price=150,
            retail_price=150, technician_price=130,
        )
        InventoryService.adjust_stock(
            organization=self.org, product_id=self.product.pk, quantity_delta=5,
            reason=StockAdjustment.Reason.OPENING, actor=self.user,
        )
        Product.objects.create(
            organization=self.other_org, tenant=self.other_org, sku='OTHER-RTR', name='Other Router', item_type='physical',
            track_stock=True, quantity=0, stock=0, measure_unit='Unit', buying_price=100, selling_price=150,
        )

    def test_api_requires_authentication_and_is_tenant_scoped(self):
        anonymous = APIClient().get('/api/inventory/products/')
        self.assertEqual(anonymous.status_code, 401)
        response = self.client.get('/api/inventory/products/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()['results']
        self.assertEqual([row['sku'] for row in payload], ['API-RTR'])
        self.assertEqual(payload[0]['technician_price'], '130.00')

    def test_restricted_api_user_cannot_see_buying_or_technician_configuration_prices(self):
        sales_user = User.objects.create_user(username='inventory-api-sales', password='pass')
        UserAccessProfile.objects.create(user=sales_user, tenant=self.org, role=UserAccessProfile.Role.TENANT_STAFF)
        IntegrationConsumer.objects.create(user=sales_user, organization=self.org, name='Restricted Inventory API')
        sales_token = Token.objects.create(user=sales_user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {sales_token.key}')

        response = client.get('/api/inventory/products/')

        self.assertEqual(response.status_code, 200)
        product_data = response.json()['results'][0]
        self.assertNotIn('buying_price', product_data)
        self.assertNotIn('technician_price', product_data)

    def test_api_invoice_write_and_full_payment_use_inventory_service(self):
        invoice_response = self.client.post('/api/inventory/invoices/', {
            'customer_id': self.customer.pk,
            'status': BillingDocument.Status.ISSUED,
            'tax_rate': '0.00',
            'discount_amount': '0.00',
            'items': [{'product_id': self.product.pk, 'quantity': '2.00', 'discount_amount': '0.00'}],
        }, format='json')
        self.assertEqual(invoice_response.status_code, 201, invoice_response.data)
        invoice_id = invoice_response.data['id']
        partial = self.client.post(f'/api/inventory/invoices/{invoice_id}/pay/', {
            'amount_paid': '1.00', 'payment_method': 'cash', 'payment_reference': 'api-partial'
        }, format='json')
        self.assertEqual(partial.status_code, 400)
        invoice = BillingDocument.objects.get(pk=invoice_id)
        paid = self.client.post(f'/api/inventory/invoices/{invoice_id}/pay/', {
            'amount_paid': str(invoice.total), 'payment_method': 'cash', 'payment_reference': 'api-full'
        }, format='json')
        self.assertEqual(paid.status_code, 201, paid.data)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3.00'))

    def test_api_requires_explicit_technician_category_and_snapshots_its_price(self):
        response = self.client.post('/api/inventory/invoices/', {
            'customer_id': self.customer.pk,
            'sale_pricing_category': BillingDocument.SalePricingCategory.TECHNICIAN,
            'status': BillingDocument.Status.DRAFT,
            'tax_rate': '0.00',
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'discount_amount': '0.00'}],
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['sale_pricing_category'], BillingDocument.SalePricingCategory.TECHNICIAN)
        self.assertEqual(Decimal(response.data['items'][0]['unit_price']), Decimal('130.00'))


class InventoryConcurrencyTests(TransactionTestCase):
    reset_sequences = False

    def setUp(self):
        if not connection.features.has_select_for_update:
            self.skipTest('Database does not support row-level select_for_update locking.')
        self.org = Organization.objects.create(name='Concurrency Tenant', slug='inventory-concurrency')
        self.user = User.objects.create_user(username='inventory-concurrent-admin', password='pass')
        UserAccessProfile.objects.create(user=self.user, tenant=self.org, role=UserAccessProfile.Role.TENANT_ADMIN)
        self.customer = Customer.objects.create(
            organization=self.org, tenant=self.org, name='Concurrent Customer', customer_type='random', location='Moshi'
        )
        self.product = Product.objects.create(
            organization=self.org, tenant=self.org, sku='CON-RTR', name='Concurrent Router', item_type='physical',
            track_stock=True, quantity=0, stock=0, measure_unit='Unit', buying_price=100, selling_price=150,
            retail_price=150,
        )
        supplier = Supplier.objects.create(
            organization=self.org, tenant=self.org, company_name='Concurrent Supplier', created_by=self.user
        )
        purchase = Purchase.objects.create(
            organization=self.org, tenant=self.org, supplier=supplier, reference_number='CON-PUR',
            purchase_date=date.today(), created_by=self.user,
        )
        PurchaseLine.objects.create(purchase=purchase, product=self.product, quantity=10, unit_cost=100)
        InventoryService.confirm_purchase(organization=self.org, purchase_id=purchase.pk, actor=self.user)
        self.invoice = BillingService.create_document(
            organization=self.org, created_by=self.user, document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk, status=BillingDocument.Status.ISSUED, tax_rate=0,
            items=[LineItemInput(product_id=self.product.pk, quantity=Decimal('2.00'))],
        )

    def test_concurrent_payment_requests_deduct_stock_once(self):
        barrier = Barrier(2)
        mutex = Lock()
        successes = []
        errors = []

        def pay(reference):
            close_old_connections()
            try:
                barrier.wait()
                receipt = BillingService.create_receipt_from_invoice(
                    organization=Organization.objects.get(pk=self.org.pk),
                    created_by=User.objects.get(pk=self.user.pk),
                    invoice_id=self.invoice.pk,
                    amount_paid=self.invoice.total,
                    payment_method='cash',
                    payment_reference=reference,
                )
                with mutex:
                    successes.append(receipt.pk)
            except Exception as exc:
                with mutex:
                    errors.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=pay, args=('concurrent-a',)), Thread(target=pay, args=('concurrent-b',))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], BillingServiceError)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('8.00'))
        self.assertEqual(StockMovement.objects.filter(product=self.product, movement_type='sale_out').count(), 1)
