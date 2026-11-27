"""Integration boundary between office cash handling and divisible packaging."""
from decimal import Decimal
from django.http import QueryDict
from django.test import TestCase
from users.permissions import membership_for
from .models import Cart, InventoryBalance, StockContainer, ContainerAllocation
from .services import CartService
from .tests_packaging_receipts import ReceiptFixture


class LocalPackagingCashIntegrationTests(ReceiptFixture, TestCase):
    def test_cash_checkout_preserves_commercial_units_and_posts_once(self):
        self.line(); self.receive()
        cart = Cart.objects.create(tenant=self.org, organization=self.org,
            created_by=self.actor, customer=self.customer)
        container = StockContainer.objects.order_by('pk').first()
        data = QueryDict('', mutable=True)
        data.update(product=str(self.product.pk), quantity='1', discount_amount='0',
            sale_packaging=str(self.package.pk), sale_configuration=str(self.product.packaging_version))
        data[f'container_{container.pk}'] = '305'
        membership = membership_for(self.actor, self.org)
        CartService.save_line(organization=self.org, cart_id=cart.pk, data=data,
            actor=self.actor, membership=membership)
        values = dict(organization=self.org, cart_id=cart.pk, actor=self.actor,
            membership=membership, payment_method='cash', cash_received=Decimal('2000'))
        receipt = CartService.checkout(**values)
        self.assertEqual(receipt.cash_received, Decimal('2000'))
        self.assertEqual(CartService.checkout(**values).pk, receipt.pk)
        line = receipt.invoice.items.get()
        self.assertEqual(line.unit_snapshot, 'Box')
        self.assertEqual(line.quantity, 1)
        self.assertEqual(line.base_quantity, 305)
        self.assertEqual(line.unit_price, Decimal('900'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, 2745)
        self.assertEqual(ContainerAllocation.objects.count(), 1)
