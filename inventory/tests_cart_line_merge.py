from decimal import Decimal
from unittest.mock import patch
from threading import Barrier, Thread

from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .tests_cart_lifecycle import CartFixture
from .models import CartLine, CartSerialSelection, InventoryBalance, StockMovement, StockUnit


class CartLineMergeFixture(CartFixture):
    def setUp(self):
        super().setUp()
        self.product.is_serialized = True
        self.product.track_stock = True
        self.product.save()
        InventoryBalance.objects.create(organization=self.org, tenant=self.org, product=self.product, quantity=3)
        self.units = [StockUnit.objects.create(organization=self.org, tenant=self.org,
                      product=self.product, serial_number=f'MERGE-{i}', unit_cost=10) for i in range(3)]
        self.sale = self.cart()
        self.url = reverse('inventory:cart_line_create', args=[self.sale.pk])

    def add(self, units, quantity=None, **extra):
        return self.client.post(self.url, {'product': self.product.pk,
            'quantity': str(quantity if quantity is not None else len(units)),
            'discount_amount': '0', 'serial_units': [u.pk for u in units], **extra})


class CartLineMergeTests(CartLineMergeFixture, TestCase):
    def test_different_serials_merge_into_one_stock_neutral_line(self):
        before = StockMovement.objects.count()
        self.assertEqual(self.add(self.units[:1]).status_code, 302)
        original = self.sale.lines.get().pk
        self.assertEqual(self.add(self.units[1:2]).status_code, 302)
        line = self.sale.lines.get()
        self.assertEqual(line.pk, original)
        self.assertEqual(line.quantity, Decimal('2'))
        self.assertEqual(set(line.serial_selections.values_list('stock_unit_id', flat=True)),
                         {u.pk for u in self.units[:2]})
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, 3)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertFalse(StockUnit.objects.exclude(status=StockUnit.Status.AVAILABLE).exists())

    def test_repeat_serial_is_actionable_and_preserves_existing_line(self):
        self.add(self.units[:1])
        response = self.add(self.units[:1])
        self.assertContains(response, 'already in this cart')
        self.assertEqual(self.sale.lines.get().quantity, 1)
        self.assertEqual(CartSerialSelection.objects.count(), 1)

    def test_total_stock_limit_is_checked_after_merge(self):
        self.add(self.units[:1])
        InventoryBalance.objects.filter(product=self.product).update(quantity=1)
        response = self.add(self.units[1:2])
        self.assertContains(response, 'Only 1')
        self.assertEqual(self.sale.lines.get().quantity, 1)
        self.assertEqual(CartSerialSelection.objects.count(), 1)

    def test_invalid_serial_count_does_not_mutate(self):
        self.add(self.units[:1])
        self.assertContains(self.add(self.units[1:2], quantity=2), 'Select one available serial')
        self.assertEqual(self.sale.lines.get().quantity, 1)

    def test_unavailable_serial_is_rejected(self):
        self.units[0].status = StockUnit.Status.SOLD
        self.units[0].save()
        self.assertEqual(self.add(self.units[:1]).status_code, 200)
        self.assertFalse(self.sale.lines.exists())

    def test_edit_replaces_serials_and_quantity(self):
        self.add(self.units[:2])
        line = self.sale.lines.get()
        self.url = reverse('inventory:cart_line_edit', args=[self.sale.pk, line.pk])
        self.assertEqual(self.add(self.units[2:]).status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 1)
        self.assertEqual(list(line.serial_selections.values_list('stock_unit_id', flat=True)), [self.units[2].pk])

    def test_edit_to_existing_product_is_validation_error(self):
        self.add(self.units[:1])
        from products.models import Product
        other = Product.objects.create(organization=self.org, tenant=self.org, name='Service', sku='MERGE-SVC',
            sales_unit=self.product.sales_unit, quantity=0, stock=0, buying_price=1, selling_price=20, track_stock=False)
        line = CartLine.objects.create(cart=self.sale, product=other, quantity=1, unit_price=20)
        self.url = reverse('inventory:cart_line_edit', args=[self.sale.pk, line.pk])
        self.assertContains(self.add(self.units[1:2]), 'already has a cart line')
        line.refresh_from_db()
        self.assertEqual(line.product_id, other.pk)

    def test_audit_failure_rolls_back_merge_and_serial_replacement(self):
        self.add(self.units[:1])
        with patch('inventory.services.audit', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.add(self.units[1:2])
        self.assertEqual(self.sale.lines.get().quantity, 1)
        self.assertEqual(list(CartSerialSelection.objects.values_list('stock_unit_id', flat=True)), [self.units[0].pk])

    def test_foreign_tenant_cart_is_hidden(self):
        from .models import Cart
        foreign = Cart.objects.create(organization=self.other, tenant=self.other, created_by=self.admin)
        self.url = reverse('inventory:cart_line_create', args=[foreign.pk])
        self.assertEqual(self.add(self.units[:1]).status_code, 404)
        self.assertFalse(foreign.lines.exists())

    def test_nonserialized_quantities_merge(self):
        self.product.is_serialized = False
        self.product.save()
        self.assertEqual(self.add([], quantity=1).status_code, 302)
        self.assertEqual(self.add([], quantity=1).status_code, 302)
        self.assertEqual(self.sale.lines.get().quantity, 2)


class CartLineMergeConcurrencyTests(CartLineMergeFixture, TransactionTestCase):
    def test_simultaneous_first_adds_keep_both_serials(self):
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(self.admin)
        barrier = Barrier(2)
        errors, statuses = [], []

        def add(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                statuses.append(clients[index].post(self.url, {
                    'product': self.product.pk, 'quantity': '1', 'discount_amount': '0',
                    'serial_units': [self.units[index].pk],
                }).status_code)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=add, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(statuses, [302, 302])
        self.assertEqual(self.sale.lines.get().quantity, 2)
        self.assertEqual(set(CartSerialSelection.objects.values_list('stock_unit_id', flat=True)),
                         {unit.pk for unit in self.units[:2]})
