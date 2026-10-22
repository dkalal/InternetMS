from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from billing.models import BillingDocument
from billing.services import BillingService, BillingServiceError
from customers.models import Customer
from users.models import Organization, TenantMembership, TenantPermissionGrant
from users.permissions import PermissionCode, can_record_payment, can_view_sales_document, validate_delegated_grant
from audit.models import AuditLog


class CustomerBalanceSummaryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Balance', slug='balance')
        self.other = Organization.objects.create(name='Other', slug='other-balance')
        self.sales = get_user_model().objects.create_user(username='balance-sales', password='pass')
        self.admin = get_user_model().objects.create_user(username='balance-admin')
        self.member = TenantMembership.objects.create(user=self.sales, tenant=self.org, base_role=TenantMembership.BaseRole.SALES)
        self.manager = TenantMembership.objects.create(user=self.admin, tenant=self.org, base_role=TenantMembership.BaseRole.ADMIN_MANAGER)
        self.customer = Customer.all_objects.create(organization=self.org, tenant=self.org, name='Balance customer')
        self.foreign = Customer.all_objects.create(organization=self.other, tenant=self.other, name='Foreign')
        self.invoice = self.doc('invoice', '100.00', due_date=timezone.localdate()-timedelta(days=1))

    def doc(self, kind, total, **kwargs):
        return BillingDocument.objects.create(organization=self.org, tenant=self.org, customer=self.customer,
            document_type=kind, total=Decimal(total), issue_date=timezone.localdate(),
            status=kwargs.pop('status', 'issued'), number=f'BAL-{BillingDocument.objects.count()}', **kwargs)

    def grant(self):
        validate_delegated_grant(actor_membership=self.manager, target_membership=self.member,
            action_code=PermissionCode.CUSTOMERS_BALANCE_SUMMARY_VIEW, scope=TenantPermissionGrant.Scope.OWN)
        return TenantPermissionGrant.objects.create(membership=self.member, granted_by=self.manager,
            action_code=PermissionCode.CUSTOMERS_BALANCE_SUMMARY_VIEW)

    def summary(self, customer=None):
        return BillingService.customer_balance_summary(user=self.sales, organization=self.org,
            customer=customer or self.customer, membership=self.member)

    def test_opt_in_and_revocation_do_not_expand_invoice_or_payment_access(self):
        with self.assertRaises(PermissionDenied): self.summary()
        self.client.force_login(self.sales)
        url = reverse('customer-detail', kwargs={'pk':self.customer.pk})
        response = self.client.get(url)
        self.assertNotIn('customer_balance_summary', response.context)
        self.assertContains(response, 'Contact Accounts')
        grant = self.grant()
        response = self.client.get(url)
        self.assertContains(response, 'Customer balance summary')
        self.assertEqual(response.context['customer_balance_summary']['currencies'][0]['outstanding'], Decimal('100'))
        self.assertFalse(can_view_sales_document(self.sales, self.invoice, membership=self.member))
        self.assertFalse(can_record_payment(self.sales, self.invoice, membership=self.member))
        self.assertNotContains(response, self.invoice.number)
        grant.delete()
        with self.assertRaises(PermissionDenied): self.summary()

    def test_ledger_currency_due_date_and_status_rules_in_bounded_queries(self):
        self.grant()
        self.doc('receipt', '20', invoice=self.invoice)
        self.doc('credit_note', '-15', corrected_invoice=self.invoice)
        self.doc('credit_note', '-99', corrected_invoice=self.invoice, status='void')
        self.doc('invoice', '40', due_date=timezone.localdate())
        self.doc('invoice', '10', currency='USD')
        self.doc('invoice', '999', status='draft')
        self.doc('invoice', '888', status='cancelled')
        self.doc('invoice', '777', is_walk_in_sale=True)
        for _ in range(12): self.doc('invoice', '1')
        with self.assertNumQueries(5): result = self.summary()
        self.assertEqual(result['currencies'], [
            {'currency':'TZS','outstanding':Decimal('117'),'overdue':Decimal('65')},
            {'currency':'USD','outstanding':Decimal('10'),'overdue':Decimal('0')},
        ])
        self.assertEqual(BillingService.invoice_remaining_balance(organization=self.org, invoice=self.invoice), Decimal('65'))

    def test_cross_tenant_and_inactive_access_denied(self):
        self.grant()
        with self.assertRaises(PermissionDenied): self.summary(self.foreign)
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse('customer-detail', kwargs={'pk':self.foreign.pk})).status_code, 404)
        self.member.is_active = False
        self.member.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied): self.summary()

    def test_sales_cannot_self_grant(self):
        with self.assertRaises(PermissionDenied):
            validate_delegated_grant(actor_membership=self.member, target_membership=self.member,
                action_code=PermissionCode.CUSTOMERS_BALANCE_SUMMARY_VIEW, scope='OWN')

    def test_team_access_grant_is_post_csrf_protected_and_audited(self):
        from django.test import Client
        url = reverse('update_member_access', kwargs={'membership_id': self.member.pk})
        data = {'base_role': 'SALES', 'scope': 'ASSIGNED',
                'permissions': [PermissionCode.CUSTOMERS_BALANCE_SUMMARY_VIEW]}
        self.client.force_login(self.sales)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(url, data).status_code, 403)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.summary()['currencies'][0]['outstanding'], Decimal('100'))
        self.assertTrue(AuditLog.objects.filter(tenant=self.org,
            action='security.member.access_changed', object_id=str(self.member.pk)).exists())

    def test_no_writes_zero_floor_and_untrusted_membership(self):
        self.grant()
        self.doc('receipt', '200', invoice=self.invoice)
        count = BillingDocument.objects.count()
        self.assertEqual(self.summary()['currencies'][0]['outstanding'], Decimal('0'))
        self.assertEqual(BillingDocument.objects.count(), count)
        with self.assertRaises(PermissionDenied):
            BillingService.customer_balance_summary(user=self.sales, organization=self.org,
                customer=self.customer, membership=self.manager)
