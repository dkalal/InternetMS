from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections
from django.http import QueryDict
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from audit.models import AuditLog
from users.permissions import membership_for
from . import tests_cart_lifecycle as fixtures
from .cart_workspace import materialize, new_token
from .models import Cart, InventoryBalance, StockMovement, StockUnit


class WorkspaceTests(fixtures.CartFixture, TestCase):
    def open_workspace(self):
        response = self.client.post(reverse('inventory:cart_create'))
        self.assertEqual(response.status_code, 302)
        return response.url

    def details(self, **overrides):
        data = {'notes': '', 'customer': '', 'walk_in_name': '', 'shipping_address': '',
                'sale_pricing_category': 'customer_tier', 'discount_amount': '0', 'tax_rate': '0'}
        return {**data, **overrides}

    def add(self, url, **overrides):
        return self.client.post(url, {'workspace_action': 'add', 'product': self.product.pk, **overrides})

    def test_open_reload_search_and_repeated_start_leave_no_drafts(self):
        for _ in range(2):
            url = self.open_workspace()
            for query in ({}, {'q': 'Router'}, {'category': '1'}):
                response = self.client.get(url, query)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'A draft is saved when')
        self.assertFalse(Cart.objects.exists())
        self.assertFalse(AuditLog.objects.filter(object_type='Cart').exists())

    def test_malformed_serial_picker_ids_do_not_raise_or_materialize(self):
        url = self.open_workspace()
        fullwidth_id = str(self.product.pk).translate(str.maketrans('0123456789', '\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19'))
        for product_id in ('bad', '\u00b2', '9' * 40, fullwidth_id):
            with self.subTest(product_id=product_id):
                response = self.client.get(url, {'line': '1', 'product': product_id})
                self.assertEqual(response.status_code, 200)
                response = self.client.post(url + '?line=1', {
                    'workspace_action': 'line', 'product': product_id,
                    'quantity': '1', 'discount_amount': '0',
                })
                self.assertEqual(response.status_code, 422)
                self.assertFalse(Cart.objects.exists())

    def test_first_add_and_retry_persist_one_line_one_unit(self):
        url = self.open_workspace()
        first = self.add(url)
        cart = Cart.objects.get()
        self.assertRedirects(first, reverse('inventory:cart_detail', args=[cart.pk]))
        repeat = self.add(url)
        self.assertEqual(first.url, repeat.url)
        self.assertEqual(cart.lines.get().quantity, 1)
        self.assertEqual(Cart.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='inventory.cart.materialized').count(), 1)
        self.assertEqual(cart.created_by, self.admin)
        self.assertFalse(StockMovement.objects.exists())

    def test_distinct_workspaces_do_not_merge_customers_or_items(self):
        a, b = self.open_workspace(), self.open_workspace()
        self.add(a)
        self.add(b)
        self.assertEqual(Cart.objects.count(), 2)

    def test_failed_and_noop_submissions_leave_no_cart_or_creation_audit(self):
        url = self.open_workspace()
        for data in (self.details(), self.details(discount_amount='-1'), self.details(discount_amount='10'),
                     {'workspace_action':'bad'}, {'workspace_action':'add', 'product':'bad'},
                     {'workspace_action':'add', 'product':'999999999999999999999999999999'}):
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 422)
            self.assertFalse(Cart.objects.exists())
            self.assertFalse(AuditLog.objects.filter(action='inventory.cart.created').exists())
            self.assertFalse(AuditLog.objects.filter(action='inventory.cart.materialized').exists())

    def test_first_customer_details_save_and_retry_preserve_creator(self):
        url = self.open_workspace()
        response = self.client.post(url, self.details(walk_in_name='Buyer one'))
        self.assertEqual(response.status_code, 302)
        cart = Cart.objects.get()
        self.client.post(url, self.details(walk_in_name='Buyer two'))
        cart.refresh_from_db()
        self.assertEqual(cart.walk_in_name, 'Buyer one')
        self.assertEqual(cart.created_by, self.admin)

    def test_ajax_first_add_returns_canonical_redirect(self):
        response = self.client.post(self.open_workspace(), {'workspace_action':'add','product':self.product.pk},
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect_url'], reverse('inventory:cart_detail', args=[Cart.objects.get().pk]))

    def test_ajax_customer_save_returns_canonical_redirect(self):
        response = self.client.post(self.open_workspace(), self.details(notes='Saved note'),
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('redirect_url', response.json())

    def test_foreign_user_and_tenant_cannot_redeem_token(self):
        url = self.open_workspace()
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.add(url).status_code, 403)
        self.assertFalse(Cart.objects.exists())

    def test_foreign_product_does_not_materialize_cart(self):
        from products.models import Product
        other = Product.objects.create(tenant=self.other, organization=self.other, name='Foreign', sku='FOREIGN',
                                       quantity=0, stock=0, buying_price=10, selling_price=20)
        self.assertEqual(self.add(self.open_workspace(), product=other.pk).status_code, 422)
        self.assertFalse(Cart.objects.exists())

    def test_same_actor_switching_tenants_cannot_redeem_workspace(self):
        from users.models import TenantMembership
        url = self.open_workspace()
        TenantMembership.objects.create(tenant=self.other, user=self.admin, base_role=TenantMembership.BaseRole.ADMIN_MANAGER)
        session = self.client.session
        session['active_tenant_id'] = self.other.pk
        session.save()
        self.assertEqual(self.add(url).status_code, 403)
        self.assertFalse(Cart.objects.unscoped().exists())

    def test_csrf_and_expired_or_tampered_tokens_fail_closed(self):
        url = self.open_workspace()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(url, {'workspace_action':'add','product':self.product.pk}).status_code, 403)
        self.assertEqual(self.client.get(url.rstrip('/') + 'bad/').status_code, 403)
        import time
        with patch('django.core.signing.time.time', return_value=time.time() - 90000):
            expired = new_token(organization=self.org, actor=self.admin)
        self.assertEqual(self.client.get(reverse('inventory:cart_workspace', args=[expired])).status_code, 403)
        self.assertFalse(Cart.objects.exists())

    def test_serial_picker_does_not_create_until_valid_serials_saved(self):
        self.product.is_serialized = True
        self.product.save()
        InventoryBalance.objects.create(tenant=self.org, organization=self.org, product=self.product, quantity=2, average_cost=10)
        unit = StockUnit.objects.create(tenant=self.org, organization=self.org, product=self.product, serial_number='WORK-1', unit_cost=10)
        url = self.open_workspace() + '?line=1&product=' + str(self.product.pk)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(Cart.objects.exists())
        payload = {'workspace_action':'line', 'product':self.product.pk, 'quantity':'1', 'discount_amount':'0'}
        self.assertEqual(self.client.post(url, payload).status_code, 422)
        self.assertFalse(Cart.objects.exists())
        response = self.client.post(url, {**payload, 'serial_units':[unit.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cart.objects.get().lines.get().serial_selections.get().stock_unit, unit)
        unit.refresh_from_db()
        self.assertEqual(unit.status, 'available')
        self.assertFalse(StockMovement.objects.exists())

    def test_audits_quantity_discount_details_removal_and_attributes_last_updater(self):
        cart = self.cart(owner=self.sales)
        self.line(cart)
        original_creator = cart.created_by_id
        adjust = reverse('inventory:cart_line_adjust', args=[cart.pk])
        self.client.post(adjust, {'product':self.product.pk,'direction':'increase'})
        self.client.post(adjust, {'product':self.product.pk,'direction':'decrease'})
        self.client.post(reverse('inventory:cart_detail', args=[cart.pk]), self.details(notes='Manager edited'))
        line = cart.lines.get()
        self.client.post(reverse('inventory:cart_line_discount', args=[cart.pk,line.pk]), {'discount_amount':'1'})
        self.client.post(reverse('inventory:cart_line_delete', args=[cart.pk,line.pk]))
        cart.refresh_from_db()
        self.assertEqual(cart.created_by_id, original_creator)
        events = AuditLog.objects.filter(action='inventory.cart.updated', object_id=str(cart.pk))
        self.assertEqual(events.count(), 5)
        self.assertEqual(set(events.values_list('actor_id', flat=True)), {self.admin.pk})
        response = self.listing()
        row = next(c for c in response.context['carts'] if c.pk == cart.pk)
        self.assertEqual(row.last_updated_by, self.admin.username)
        self.assertContains(response, 'Created by')
        self.assertFalse(StockMovement.objects.exists())

    def test_historical_actor_is_unknown_and_get_does_not_invent_audit(self):
        cart = self.cart()
        response = self.listing()
        row = response.context['carts'][0]
        self.assertIsNone(row.last_updated_by)
        self.assertContains(response, 'Unknown')
        self.assertFalse(AuditLog.objects.filter(action='inventory.cart.updated').exists())

    def test_line_edit_audit_preserves_values_before_modelform_validation(self):
        cart = self.cart()
        line = self.line(cart)
        response = self.client.post(reverse('inventory:cart_line_edit', args=[cart.pk,line.pk]),
                                    {'product':self.product.pk, 'quantity':'3', 'discount_amount':'0'})
        self.assertEqual(response.status_code, 302)
        event = AuditLog.objects.get(action='inventory.cart.updated', object_id=str(cart.pk))
        self.assertEqual(event.old_value['quantity'], '1.00')
        self.assertEqual(event.new_value['quantity'], '3')

    def test_completed_by_is_cashier_and_does_not_replace_creator(self):
        from .services import CartService
        cart = self.cart(owner=self.sales)
        self.line(cart)
        CartService.checkout(organization=self.org, cart_id=cart.pk, actor=self.admin,
                             membership=membership_for(self.admin,self.org), payment_method='cash')
        cart.refresh_from_db()
        self.assertEqual(cart.created_by, self.sales)
        row = self.listing(tab='all').context['carts'][0]
        self.assertEqual(row.completed_by, self.admin.username)

    def test_audit_failure_rolls_back_first_cart_and_line(self):
        token = new_token(organization=self.org, actor=self.admin)
        data = QueryDict('', mutable=True)
        data['product'] = str(self.product.pk)
        with patch('inventory.services.audit', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                materialize(token=token, organization=self.org, actor=self.admin,
                            membership=membership_for(self.admin,self.org), action='add', data=data)
        self.assertFalse(Cart.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action='inventory.cart.materialized').exists())


class WorkspaceConcurrencyTests(fixtures.CartFixture, TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_first_adds_share_one_cart_without_doubling_quantity(self):
        token = new_token(organization=self.org, actor=self.admin)
        barrier = Barrier(2)
        def save():
            close_old_connections()
            try:
                data = QueryDict('', mutable=True)
                data['product'] = str(self.product.pk)
                barrier.wait(timeout=10)
                return materialize(token=token, organization=self.org, actor=self.admin,
                                   membership=membership_for(self.admin,self.org), action='add', data=data).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(save) for _ in range(2)]
            ids = [f.result(timeout=30) for f in futures]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Cart.objects.count(), 1)
        self.assertEqual(Cart.objects.get().lines.get().quantity, 1)
