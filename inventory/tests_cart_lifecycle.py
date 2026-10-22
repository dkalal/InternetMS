from datetime import timedelta
from decimal import Decimal
from io import StringIO
from threading import Barrier, Event, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from billing.models import BillingDocument
from products.models import Product, UnitOfMeasure
from users.models import Organization, UserAccessProfile
from .cart_lifecycle import cleanup_candidate, genuinely_empty, stale
from .models import Cart, CartLine, InventoryBalance, InventorySale, StockMovement
from .services import CartService, InventoryError

User = get_user_model()


class CartFixture:
    def setUp(self):
        self.org = Organization.objects.create(name='Lifecycle', slug='lifecycle')
        self.other = Organization.objects.create(name='Other lifecycle', slug='other-lifecycle')
        self.admin = User.objects.create_user(username='lifecycle-admin', password='pass')
        self.sales = User.objects.create_user(username='lifecycle-sales', password='pass')
        UserAccessProfile.objects.create(user=self.admin, tenant=self.org, role=UserAccessProfile.Role.TENANT_ADMIN)
        UserAccessProfile.objects.create(user=self.sales, tenant=self.org, role=UserAccessProfile.Role.TENANT_STAFF)
        unit = UnitOfMeasure.objects.create(organization=self.org, tenant=self.org, name='Piece', symbol='pc')
        self.product = Product.objects.create(organization=self.org, tenant=self.org, name='Router', sku='LIFE',
            sales_unit=unit, quantity=0, stock=0, buying_price=Decimal('10'), selling_price=Decimal('20'), track_stock=False)
        self.client.force_login(self.admin)
        self.now = timezone.now()

    def cart(self, *, age=None, owner=None, **fields):
        cart = Cart.objects.create(organization=self.org, tenant=self.org, created_by=owner or self.admin, **fields)
        if age is not None:
            Cart.objects.filter(pk=cart.pk).update(updated_at=self.now - age)
            cart.refresh_from_db()
        return cart

    def line(self, cart):
        return CartLine.objects.create(cart=cart, product=self.product, quantity=1, unit_price=20)

    def listing(self, **params):
        return self.client.get(reverse('inventory:cart_list'), params)


class CartLifecycleTests(CartFixture, TestCase):
    def test_default_active_and_exact_stale_subset(self):
        normal = self.cart(age=timedelta(days=7) - timedelta(microseconds=1))
        old = self.cart(age=timedelta(days=7))
        self.cart(status=Cart.Status.ABANDONED)
        with patch('inventory.views.timezone.now', return_value=self.now):
            active = self.listing()
            stale_page = self.listing(tab='stale')
        self.assertEqual({c.pk for c in active.context['carts']}, {normal.pk, old.pk})
        self.assertEqual([c.pk for c in stale_page.context['carts']], [old.pk])
        self.assertTrue(stale(old, now=self.now))
        self.assertFalse(stale(normal, now=self.now))

    def test_search_owner_dates_and_stable_pagination(self):
        for i in range(26):
            self.cart(age=timedelta(days=1), walk_in_name='Target')
        first = self.listing(q='Target', owner=self.admin.pk, **{'from': (self.now - timedelta(days=2)).date().isoformat()})
        second = self.listing(q='Target', page=2)
        ids = [c.pk for c in first.context['carts']]
        self.assertEqual(len(ids), 25)
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(len(second.context['carts']), 1)
        self.assertNotIn(second.context['carts'][0].pk, ids)
        self.assertContains(first, 'q=Target')
        self.assertContains(first, 'Last activity')
        self.assertEqual(len(self.listing(q=str(ids[0])).context['carts']), 1)
        self.assertContains(self.listing(**{'from':'invalid'}), 'Enter a valid last activity date')

    def test_sales_scope_counts_search_owners_and_every_endpoint(self):
        own = self.cart(owner=self.sales)
        hidden = self.cart(walk_in_name='Secret buyer')
        foreign = Cart.objects.create(organization=self.other, tenant=self.other, created_by=self.sales)
        self.client.force_login(self.sales)
        response = self.listing(tab='all')
        self.assertEqual([c.pk for c in response.context['carts']], [own.pk])
        self.assertEqual(dict((v, n) for v, _, n in response.context['tabs'])['all'], 1)
        self.assertEqual(list(response.context['owners'])[0]['created_by_id'], self.sales.pk)
        self.assertFalse(self.listing(q='Secret buyer').context['carts'])
        for cart in (hidden, foreign):
            for name in ('cart_detail', 'cart_abandon', 'cart_restore'):
                self.assertEqual(self.client.post(reverse('inventory:'+name, args=[cart.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse('inventory:cart_line_adjust', args=[cart.pk]), {'product':self.product.pk}).status_code, 404)
            with self.assertRaises(PermissionDenied):
                CartService.abandon(organization=self.org, cart_id=cart.pk, actor=self.sales)

    def test_linked_document_search_and_immutable_checkout_retry(self):
        cart = self.cart()
        self.line(cart)
        quote = CartService.convert(organization=self.org, cart_id=cart.pk, actor=self.admin, target='quotation')
        self.assertEqual([c.pk for c in self.listing(tab='converted', q=quote.number).context['carts']], [cart.pk])
        cart.refresh_from_db()
        old_activity = cart.updated_at
        response = self.client.post(reverse('inventory:cart_convert', args=[cart.pk,'quotation']), {'discount_amount':'5','notes':'tamper'})
        self.assertEqual(response.status_code,302)
        cart.refresh_from_db()
        self.assertEqual(cart.notes, '')
        self.assertEqual(cart.updated_at,old_activity)
        self.assertContains(self.client.get(reverse('inventory:cart_detail',args=[cart.pk])), quote.number)
        for name, args, data in (
            ('cart_detail',[cart.pk],{'notes':'tamper'}),
            ('cart_line_create',[cart.pk],{'product':self.product.pk,'quantity':1}),
            ('cart_line_edit',[cart.pk,cart.lines.get().pk],{'quantity':2}),
            ('cart_line_discount',[cart.pk,cart.lines.get().pk],{'discount_amount':1}),
            ('cart_line_adjust',[cart.pk],{'product':self.product.pk,'direction':'increase'}),
            ('cart_line_delete',[cart.pk,cart.lines.get().pk],{}),
            ('cart_abandon',[cart.pk],{}),('cart_restore',[cart.pk],{}),
        ):
            self.client.post(reverse('inventory:'+name,args=args),data)
        cart.refresh_from_db()
        self.assertEqual(cart.status,Cart.Status.CONVERTED)
        self.assertEqual(cart.lines.get().quantity,1)
        self.assertEqual(cart.notes,'')
        line=cart.lines.get()
        with self.assertRaises(ValidationError):
            line.save()
        self.product.is_active=False
        self.product.save()
        self.assertContains(self.client.get(reverse('inventory:cart_detail',args=[cart.pk])),self.product.name)

    def test_start_post_csrf_recent_empty_reuse_and_separation(self):
        csrf_client=Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        url=reverse('inventory:cart_create')
        self.assertEqual(csrf_client.post(url).status_code,403)
        self.assertEqual(self.client.get(url).status_code,404)
        first=CartService.start(organization=self.org,actor=self.admin)
        self.assertEqual(CartService.start(organization=self.org,actor=self.admin).pk,first.pk)
        self.line(first)
        second=CartService.start(organization=self.org,actor=self.admin)
        self.assertNotEqual(second.pk,first.pk)
        self.assertNotEqual(CartService.start(organization=self.org,actor=self.sales).pk,second.pk)
        Cart.objects.filter(pk=second.pk).update(updated_at=self.now-timedelta(hours=24))
        self.assertNotEqual(CartService.start(organization=self.org,actor=self.admin).pk,second.pk)

    def test_empty_policy_is_conservative(self):
        for values in ({'notes':'note'}, {'shipping_address':'address'}, {'walk_in_name':'Buyer'},
                       {'tax_rate':1}, {'discount_amount':1}, {'sale_pricing_category':'standard'},
                       {'converted_at':self.now}, {'restored_at':self.now},
                       {'abandoned_at':self.now}, {'abandonment_reason':'Keep history'}):
            with self.subTest(values=values):
                self.assertFalse(genuinely_empty(self.cart(**values)))
        self.assertTrue(genuinely_empty(self.cart(notes='  ',walk_in_name='  ')))
        cart=self.cart()
        self.line(cart)
        self.assertFalse(genuinely_empty(cart))

    def test_lines_and_details_change_activity_but_get_and_noop_do_not(self):
        cart=self.cart(age=timedelta(days=8))
        old=cart.updated_at
        self.client.get(reverse('inventory:cart_detail',args=[cart.pk]))
        cart.refresh_from_db()
        self.assertEqual(cart.updated_at,old)
        line=self.line(cart)
        cart.refresh_from_db()
        self.assertGreater(cart.updated_at,old)
        Cart.objects.filter(pk=cart.pk).update(updated_at=old)
        line.quantity=2
        line.save()
        cart.refresh_from_db()
        self.assertGreater(cart.updated_at,old)
        Cart.objects.filter(pk=cart.pk).update(updated_at=old)
        line.delete()
        cart.refresh_from_db()
        self.assertGreater(cart.updated_at,old)
        url=reverse('inventory:cart_detail',args=[cart.pk])
        self.client.post(url,{'sale_pricing_category':'customer_tier','walk_in_name':'Changed','discount_amount':'0','tax_rate':'0'})
        cart.refresh_from_db()
        timestamp=cart.updated_at
        self.client.post(url,{'sale_pricing_category':'customer_tier','walk_in_name':'Changed','discount_amount':'0','tax_rate':'0'})
        cart.refresh_from_db()
        self.assertEqual(cart.updated_at,timestamp)

    def test_manual_abandon_restore_preserves_items_metadata_and_stock(self):
        cart=self.cart(notes='Keep this')
        line=self.line(cart)
        before=(BillingDocument.objects.count(),StockMovement.objects.count(),InventoryBalance.objects.count())
        CartService.abandon(organization=self.org,cart_id=cart.pk,actor=self.admin,reason='Later')
        cart.refresh_from_db()
        self.assertEqual(cart.abandonment_reason,'Later')
        self.assertEqual(cart.abandoned_by,self.admin)
        self.assertIsNotNone(cart.abandoned_at)
        with self.assertRaises(PermissionDenied):
            CartService.restore(organization=self.org,cart_id=cart.pk,actor=self.sales)
        CartService.restore(organization=self.org,cart_id=cart.pk,actor=self.admin)
        cart.refresh_from_db()
        self.assertEqual(cart.notes,'Keep this')
        self.assertEqual(cart.lines.get().pk,line.pk)
        self.assertEqual(cart.restored_by,self.admin)
        self.assertGreaterEqual(cart.updated_at,cart.restored_at)
        self.assertTrue(AuditLog.objects.filter(action='inventory.cart.restored',object_id=str(cart.pk)).exists())
        self.assertEqual(before,(BillingDocument.objects.count(),StockMovement.objects.count(),InventoryBalance.objects.count()))
        self.product.selling_price=30
        self.product.save()
        with self.assertRaisesMessage(InventoryError,'Pricing'):
            CartService.convert(organization=self.org,cart_id=cart.pk,actor=self.admin,target='quotation')
        self.assertEqual(cart.lines.get().unit_price,20)

    def test_cleanup_exact_boundaries_and_history(self):
        recent=self.cart(age=timedelta(hours=24)-timedelta(microseconds=1))
        empty=self.cart(age=timedelta(hours=24))
        nonempty_recent=self.cart(age=timedelta(days=30)-timedelta(microseconds=1),notes='Keep')
        nonempty=self.cart(age=timedelta(days=30),notes='Keep')
        history=self.cart(age=timedelta(days=90),status='abandoned')
        converted=self.cart(age=timedelta(days=90),status='converted')
        self.assertEqual(cleanup_candidate(cart_id=recent.pk,now=self.now,apply=True)[0],'skipped')
        self.assertEqual(cleanup_candidate(cart_id=empty.pk,now=self.now,apply=True)[0],'deleted')
        self.assertEqual(cleanup_candidate(cart_id=nonempty_recent.pk,now=self.now,apply=True)[0],'skipped')
        self.assertEqual(cleanup_candidate(cart_id=nonempty.pk,now=self.now,apply=True)[0],'abandoned')
        for cart in (history,converted,nonempty):
            self.assertEqual(cleanup_candidate(cart_id=cart.pk,now=self.now,apply=True)[0],'skipped')
        nonempty.refresh_from_db()
        self.assertEqual(nonempty.abandoned_at,self.now)
        self.assertIsNone(nonempty.abandoned_by)
        self.assertTrue(Cart.objects.filter(pk=history.pk).exists())

    def test_linked_draft_excluded_from_cleanup_abandon_restore(self):
        cart=self.cart()
        self.line(cart)
        doc=CartService.convert(organization=self.org,cart_id=cart.pk,actor=self.admin,target='invoice')
        Cart.objects.filter(pk=cart.pk).update(status='draft',updated_at=self.now-timedelta(days=40))
        self.assertEqual(cleanup_candidate(cart_id=cart.pk,now=self.now,apply=True)[1],'business_link_review')
        with self.assertRaises(InventoryError):
            CartService.abandon(organization=self.org,cart_id=cart.pk,actor=self.admin)
        Cart.objects.filter(pk=cart.pk).update(status='abandoned')
        with self.assertRaises(InventoryError):
            CartService.restore(organization=self.org,cart_id=cart.pk,actor=self.admin)
        self.assertTrue(InventorySale.objects.filter(invoice=doc).exists())
        self.assertTrue(BillingDocument.objects.filter(pk=doc.pk).exists())

    def test_command_dry_run_apply_tenant_batch_idempotency_audit(self):
        a=self.cart(age=timedelta(days=2))
        b=self.cart(age=timedelta(days=40),notes='Keep')
        foreign=Cart.objects.create(organization=self.other,tenant=self.other,created_by=self.admin)
        Cart.objects.filter(pk=foreign.pk).update(updated_at=self.now-timedelta(days=40))
        out=StringIO()
        before=AuditLog.objects.count()
        call_command('cleanup_carts',tenant=self.org.slug,stdout=out)
        self.assertIn('eligible=2',out.getvalue())
        self.assertEqual(AuditLog.objects.count(),before)
        self.assertTrue(Cart.objects.filter(pk=a.pk).exists())
        out=StringIO()
        call_command('cleanup_carts',tenant=self.org.slug,apply=True,batch_size=1,stdout=out)
        self.assertIn('deleted=1',out.getvalue())
        self.assertIn('next_after_id='+str(a.pk),out.getvalue())
        call_command('cleanup_carts',tenant=self.org.slug,apply=True,stdout=StringIO())
        before=AuditLog.objects.count()
        call_command('cleanup_carts',tenant=self.org.slug,apply=True,stdout=StringIO())
        self.assertEqual(AuditLog.objects.count(),before)
        b.refresh_from_db()
        self.assertEqual(b.status,'abandoned')
        self.assertTrue(Cart.objects.filter(pk=foreign.pk).exists())
        log=AuditLog.objects.get(action='inventory.cart.empty_deleted',object_id=str(a.pk))
        self.assertNotIn('notes',str(log.metadata))

    def test_inaccessible_document_link_does_not_leak_through_own_cart(self):
        cart = self.cart()
        self.line(cart)
        doc = CartService.convert(organization=self.org, cart_id=cart.pk, actor=self.admin, target='quotation')
        Cart.objects.filter(pk=cart.pk).update(created_by=self.sales)
        self.client.force_login(self.sales)
        response = self.listing(tab='all', q=doc.number)
        self.assertFalse(response.context['carts'])
        self.assertEqual(dict((v,n) for v,_,n in response.context['tabs'])['all'], 0)
        # The searched value is legitimately echoed in the filter input.
        self.assertNotContains(self.listing(tab='all'), doc.number)
        self.assertEqual(self.client.get(reverse('inventory:cart_detail', args=[cart.pk])).status_code,404)

    def test_selected_customer_and_reverse_sale_are_never_empty(self):
        from customers.models import Customer
        customer = Customer.objects.create(organization=self.org,tenant=self.org,name='Named buyer',customer_type='random',location='Moshi')
        self.assertFalse(genuinely_empty(self.cart(customer=customer)))
        cart = self.cart()
        self.line(cart)
        CartService.convert(organization=self.org,cart_id=cart.pk,actor=self.admin,target='invoice')
        Cart.objects.filter(pk=cart.pk).update(invoice=None,status='draft',converted_at=None,updated_at=self.now-timedelta(days=40))
        cart.refresh_from_db()
        self.assertFalse(genuinely_empty(cart))
        self.assertEqual(cleanup_candidate(cart_id=cart.pk,now=self.now,apply=True)[1],'business_link_review')
        with self.assertRaises(InventoryError):
            CartService.convert(organization=self.org,cart_id=cart.pk,actor=self.admin,target='quotation')

    def test_known_activity_reconciliation_uses_actual_recorded_time_only(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        cart = self.cart()
        line = self.line(cart)
        recorded = self.now - timedelta(days=2)
        CartLine.objects.filter(pk=line.pk).update(updated_at=recorded)
        Cart.objects.filter(pk=cart.pk).update(updated_at=self.now-timedelta(days=40))
        migration = import_module('inventory.migrations.0015_cart_known_line_activity')
        with connection.schema_editor(atomic=False) as editor:
            migration.preserve_known_line_activity(apps,editor)
        cart.refresh_from_db()
        self.assertEqual(cart.updated_at,recorded)
        self.assertIsNone(cart.abandoned_at)
        self.assertIsNone(cart.restored_at)

    def test_support_access_requires_session_and_preserves_authorized_flow(self):
        from users.models import SupportAccessSession, TenantMembership
        support_user = User.objects.create_user(username='cart-support',password='pass')
        TenantMembership.objects.create(user=support_user,tenant=self.other,base_role=TenantMembership.BaseRole.SUPER_ADMIN)
        self.client.force_login(support_user)
        url = reverse('inventory:cart_create')
        self.assertEqual(self.client.post(url).status_code,403)
        session = self.client.session
        support = SupportAccessSession.objects.create(actor=support_user,tenant=self.org,reason='Investigate cart issue',session_key=session.session_key)
        session['support_access_session_id'] = support.pk
        session.save()
        response = self.client.post(url)
        self.assertEqual(response.status_code,302)
        cart = Cart.objects.unscoped().get(created_by=support_user)
        self.assertEqual(cart.tenant,self.org)
        self.client.post(reverse('inventory:cart_abandon',args=[cart.pk]))
        self.assertEqual(self.client.post(reverse('inventory:cart_restore',args=[cart.pk])).status_code,302)
        cart.refresh_from_db()
        self.assertEqual(cart.status,'draft')
        self.assertTrue(AuditLog.objects.unscoped().filter(action='security.support_request',tenant=self.org).exists())

    def test_cleanup_audit_failure_rolls_back_deletion(self):
        cart = self.cart(age=timedelta(days=2))
        with patch('inventory.services.audit',side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):
                cleanup_candidate(cart_id=cart.pk,now=self.now,apply=True)
        self.assertTrue(Cart.objects.filter(pk=cart.pk).exists())


class CartLifecycleConcurrencyTests(CartFixture, TransactionTestCase):
    def test_simultaneous_start_creates_one_draft(self):
        barrier=Barrier(2)
        results=[]
        errors=[]
        def start():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                results.append(CartService.start(organization=self.org,actor=self.admin).pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        threads=[Thread(target=start) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=20)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors,[])
        self.assertEqual(len(results),2)
        self.assertEqual(results[0],results[1])

    def race_cleanup(self,convert=False):
        cart=self.cart(age=timedelta(days=40))
        if convert:
            self.line(cart)
            Cart.objects.filter(pk=cart.pk).update(updated_at=self.now-timedelta(days=40))
        locked=Event()
        release=Event()
        errors=[]
        outcomes=[]
        def edit():
            close_old_connections()
            try:
                with transaction.atomic():
                    obj=Cart.objects.select_for_update().get(pk=cart.pk)
                    if convert:
                        CartService.convert(organization=self.org,cart_id=obj.pk,actor=self.admin,target='quotation')
                    else:
                        self.line(obj)
                    locked.set()
                    if not release.wait(timeout=10): raise RuntimeError('Release timed out')
            except Exception as exc:
                errors.append(exc)
                locked.set()
            finally:
                close_old_connections()
        def cleanup():
            close_old_connections()
            try:
                outcomes.append(cleanup_candidate(cart_id=cart.pk,now=self.now,apply=True))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        writer=Thread(target=edit)
        writer.start()
        self.assertTrue(locked.wait(timeout=10))
        worker=Thread(target=cleanup)
        worker.start()
        release.set()
        writer.join(timeout=20)
        worker.join(timeout=20)
        self.assertFalse(writer.is_alive() or worker.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(outcomes[0][0],'skipped')
        cart.refresh_from_db()
        self.assertEqual(cart.status,'converted' if convert else 'draft')
        self.assertEqual(cart.lines.count(),1)

    def test_cleanup_waits_for_edit_and_rechecks_activity(self):
        self.race_cleanup()

    def test_cleanup_waits_for_conversion_and_preserves_history(self):
        self.race_cleanup(convert=True)
