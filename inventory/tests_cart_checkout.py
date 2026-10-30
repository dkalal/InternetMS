from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from billing.models import BillingDocument
from products.models import Product, UnitOfMeasure
from users.models import Organization, TenantMembership
from .models import Cart, CartLine, CartSerialSelection, InventoryBalance, InventorySale, StockMovement, StockUnit
from .services import CartService


class CartCheckoutTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Checkout', slug='checkout')
        self.user = get_user_model().objects.create_user(username='checkout-admin', password='pass')
        self.membership = TenantMembership.objects.create(
            tenant=self.org, user=self.user, base_role=TenantMembership.BaseRole.ADMIN_MANAGER,
        )
        unit = UnitOfMeasure.objects.create(organization=self.org, tenant=self.org, name='Piece', symbol='pc')
        self.product = Product.objects.create(
            organization=self.org, tenant=self.org, name='Router', sku='CHECKOUT', sales_unit=unit,
            buying_price=Decimal('50'), selling_price=Decimal('100'), technician_price=Decimal('90'),
            track_stock=True, quantity=0, stock=0,
        )
        self.balance = InventoryBalance.objects.create(
            organization=self.org, tenant=self.org, product=self.product,
            quantity=Decimal('5'), average_cost=Decimal('50'),
        )
        self.cart = Cart.objects.create(organization=self.org, tenant=self.org, created_by=self.user)
        CartLine.objects.create(cart=self.cart, product=self.product, quantity=2, unit_price=100)
        self.url = reverse('inventory:cart_checkout', args=[self.cart.pk])
        self.client.force_login(self.user)
        self.payload = {
            'customer': '', 'walk_in_name': 'Cash buyer', 'sale_pricing_category': 'customer_tier',
            'discount_amount': '0', 'tax_rate': '0', 'notes': 'Sale note',
            'payment_method': 'cash', 'payment_reference': '',
        }

    def receipts(self):
        return BillingDocument.objects.filter(document_type='receipt')

    def assert_draft_unchanged(self):
        self.cart.refresh_from_db()
        self.balance.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.Status.DRAFT)
        self.assertIsNone(self.cart.invoice_id)
        self.assertEqual(self.cart.walk_in_name, '')
        self.assertEqual(self.balance.quantity, Decimal('5'))
        self.assertFalse(BillingDocument.objects.exists())
        self.assertFalse(InventorySale.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_checkout_uses_full_server_total_and_current_local_date(self):
        with patch('inventory.services.timezone.localdate', return_value=date(2026, 10, 29)):
            response = self.client.post(self.url, {
                **self.payload, 'amount_paid': '1', 'payment_date': '2000-01-01',
            })
        receipt = self.receipts().get()
        self.assertRedirects(response, reverse('billing:document_detail', args=['receipt', receipt.pk]))
        self.assertEqual(receipt.total, Decimal('200'))
        self.assertEqual(receipt.payment_date, date(2026, 10, 29))
        self.assertEqual(receipt.payment_method, 'cash')
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.quantity, Decimal('3'))
        self.assertEqual(StockMovement.objects.filter(movement_type='sale_out').count(), 1)

    def test_retry_without_reference_returns_same_receipt_without_changing_details(self):
        first = self.client.post(self.url, self.payload)
        second = self.client.post(self.url, {**self.payload, 'walk_in_name': 'Changed', 'payment_method': 'bank'})
        self.assertEqual(first.url, second.url)
        self.assertEqual(self.receipts().count(), 1)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.walk_in_name, 'Cash buyer')
        self.assertEqual(self.receipts().get().payment_method, 'cash')

    def test_invalid_method_preserves_input_without_mutation(self):
        response = self.client.post(self.url, {**self.payload, 'payment_method': 'invalid', 'payment_reference': 'KEEP-ME'})
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, 'KEEP-ME', status_code=422)
        self.assertContains(response, 'Cash buyer', status_code=422)
        self.assert_draft_unchanged()

    def test_insufficient_stock_rolls_back_invoice_receipt_and_cart_details(self):
        self.balance.quantity = 1
        self.balance.save(update_fields=['quantity'])
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 422)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.Status.DRAFT)
        self.assertEqual(self.cart.walk_in_name, '')
        self.assertFalse(BillingDocument.objects.exists())
        self.assertFalse(InventorySale.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.quantity, 1)

    def test_duplicate_reference_on_another_sale_rolls_back(self):
        self.client.post(self.url, {**self.payload, 'payment_reference': 'TX-ONE'})
        other = Cart.objects.create(organization=self.org, tenant=self.org, created_by=self.user)
        CartLine.objects.create(cart=other, product=self.product, quantity=1, unit_price=100)
        response = self.client.post(reverse('inventory:cart_checkout', args=[other.pk]), {
            **self.payload, 'payment_reference': 'TX-ONE',
        })
        self.assertEqual(response.status_code, 422)
        other.refresh_from_db()
        self.assertEqual(other.status, Cart.Status.DRAFT)
        self.assertEqual(self.receipts().count(), 1)
        self.assertEqual(BillingDocument.objects.filter(document_type='invoice').count(), 1)

    def test_other_owner_is_denied(self):
        seller = get_user_model().objects.create_user(username='other-seller')
        TenantMembership.objects.create(tenant=self.org, user=seller, base_role=TenantMembership.BaseRole.SALES)
        self.client.force_login(seller)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 404)
        self.assert_draft_unchanged()

    def test_service_denies_other_tenant(self):
        other = Organization.objects.create(name='Other', slug='other-checkout')
        with self.assertRaises(PermissionDenied):
            CartService.checkout(organization=other, cart_id=self.cart.pk, actor=self.user, payment_method='cash')
        self.assert_draft_unchanged()

    def test_payment_permission_is_required(self):
        original = __import__('users.permissions', fromlist=['has_tenant_permission']).has_tenant_permission
        def allowed(user, tenant, permission, **kwargs):
            return permission != 'payments.record_own' and original(user, tenant, permission, **kwargs)
        with patch('users.permissions.has_tenant_permission', side_effect=allowed):
            self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
        self.assert_draft_unchanged()

    def test_payment_fields_only_and_quotation_without_method(self):
        response = self.client.get(reverse('inventory:cart_detail', args=[self.cart.pk]))
        self.assertContains(response, 'name="payment_method"')
        self.assertContains(response, 'name="payment_reference"')
        self.assertNotContains(response, 'name="amount_paid"')
        self.assertNotContains(response, 'name="payment_date"')
        response = self.client.post(reverse('inventory:cart_convert', args=[self.cart.pk, 'quotation']), {
            **self.payload, 'payment_method': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BillingDocument.objects.get().document_type, 'quotation')
        self.assertFalse(StockMovement.objects.exists())

    def test_get_does_not_mutate(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assert_draft_unchanged()

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, self.payload).status_code, 403)
        self.assert_draft_unchanged()

    def test_unavailable_serial_rolls_back_entire_checkout(self):
        self.product.is_serialized = True
        self.product.save(update_fields=['is_serialized'])
        line = self.cart.lines.get()
        for index in range(2):
            unit = StockUnit.objects.create(
                tenant=self.org, organization=self.org, product=self.product,
                serial_number=f'CHECKOUT-{index}', unit_cost=50,
                status=StockUnit.Status.SOLD if index == 1 else StockUnit.Status.AVAILABLE,
            )
            CartSerialSelection.objects.create(tenant=self.org, cart_line=line, stock_unit=unit)
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 422)
        self.assert_draft_unchanged()
        self.assertEqual(StockUnit.objects.filter(status=StockUnit.Status.AVAILABLE).count(), 1)


class ConcurrentCartCheckoutTests(TransactionTestCase):
    setUp = CartCheckoutTests.setUp

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_cash_checkouts_issue_one_receipt_and_movement(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        barrier = Barrier(2)

        def checkout():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                org = Organization.objects.get(pk=self.org.pk)
                barrier.wait(timeout=10)
                return CartService.checkout(
                    organization=org, cart_id=self.cart.pk, actor=user, payment_method='cash',
                ).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(checkout) for _ in range(2)]
            receipts = [future.result(timeout=30) for future in futures]
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(BillingDocument.objects.filter(document_type='receipt').count(), 1)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.quantity, Decimal('3'))
