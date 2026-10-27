"""Classified sample maintenance: destructive operations run only in a test DB."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from audit.models import AuditLog
from billing.models import BillingDocument, BillingLineItem, CustomerSubscription, DocumentSequence, SubscriptionPeriod
from billing.services import BillingService, LineItemInput
from customers.models import Customer, CustomerSite, InternetCustomer
from inventory import handover_cleanup as cleanup
from inventory.models import InventoryBalance, InventorySale, InventorySaleLine, StockMovement
from products.models import Product
from services.models import Package
from users.models import Organization, SupportAccessSession, TenantMembership, UserAccessProfile


class ClassifiedHandoverTests(TransactionTestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'HANDOVER_BUILD_COMMIT': 'a' * 40})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.org = Organization.objects.create(name='Classified', slug='classified')
        self.other = Organization.objects.create(name='Other classified', slug='other-classified')
        self.actor = get_user_model().objects.create_superuser(username='classified-admin', password='pass')
        self.membership = TenantMembership.objects.create(tenant=self.org, user=self.actor, base_role='SUPER_ADMIN')
        self.support = SupportAccessSession.objects.create(tenant=self.org, actor=self.actor,
            reason='Synthetic classified cleanup rehearsal', session_key='synthetic-classified-session')
        self.common = dict(tenant_slug=self.org.slug, actor_id=self.actor.pk, support_id=self.support.pk)
        self.customer = Customer.objects.create(tenant=self.org, organization=self.org,
            name='Retained real subscriber', customer_type='internet')
        self.profile = InternetCustomer.objects.create(tenant=self.org, customer=self.customer, package_type='indoor')
        self.site = CustomerSite.objects.create(tenant=self.org, organization=self.org, customer=self.customer, name='Retained site')
        self.package = Package.objects.create(tenant=self.org, organization=self.org, name='Retained package',
            package_type='indoor', speed='10 Mbps', monthly_fee=100, setup_fee=0)
        self.subscription = CustomerSubscription.objects.create(tenant=self.org, organization=self.org,
            customer=self.customer, site=self.site, package=self.package,
            start_date=timezone.localdate(), monthly_fee_at_signup=100)
        self.real_invoice = self.document('REAL-I')
        self.real_invoice.total = 100
        self.real_invoice.save()
        self.real_item = self.line(self.real_invoice, package=self.package, subscription=self.subscription)
        BillingDocument.objects.unscoped().filter(tenant=self.org, pk=self.real_invoice.pk).update(status='partially_paid')
        self.real_receipt = self.document('REAL-R', document_type='receipt', status='issued', invoice=self.real_invoice, total=30,
            payment_reference='retained-real-payment')
        self.period = SubscriptionPeriod.objects.create(tenant=self.org, organization=self.org,
            subscription=self.subscription, period_start=timezone.localdate(), period_end=timezone.localdate() + timedelta(days=30),
            invoice=self.real_invoice, status='invoiced', original_amount=100, final_amount=100)
        self.counter = DocumentSequence.objects.create(tenant=self.org, organization=self.org,
            document_type='invoice', year=2026, last_number=101)
        self.old_audit = AuditLog.objects.create(tenant=self.org, organization=self.org, actor=self.actor,
            action='retained.real.history', object_type='Customer', object_id=str(self.customer.pk))
        self.product = Product.objects.create(tenant=self.org, organization=self.org, name='Sample catalog item',
            sku='SAMPLE', quantity=10, buying_price=10, selling_price=20)
        self.balance = InventoryBalance.objects.create(tenant=self.org, organization=self.org,
            product=self.product, quantity=10, average_cost=10)
        self.movement = StockMovement.objects.create(tenant=self.org, organization=self.org,
            product=self.product, movement_type='opening', quantity=10, balance_after=10, unit_cost=10)
        self.invoice = self.document('SAMPLE-I', balance_brought_forward=70)
        self.product_line = self.line(self.invoice, product=self.product)
        self.package_line = self.line(self.invoice, package=self.package)
        self.manual_line = self.line(self.invoice)
        self.root = self.document('SAMPLE-Q', document_type='quotation')
        self.root_line = self.line(self.root, product=self.product)
        BillingDocument.objects.unscoped().filter(tenant=self.org, pk=self.root.pk).update(root_quotation=self.root)
        self.version = self.document('SAMPLE-Q-V2', document_type='quotation', parent_quotation=self.root,
            root_quotation=self.root, status='sent')
        self.version_line = self.line(self.version, package=self.package)
        self.sale = InventorySale.objects.create(tenant=self.org, organization=self.org, invoice=self.invoice)
        self.doc_ids = [self.invoice.pk, self.root.pk, self.version.pk]
        self.line_ids = [self.product_line.pk, self.package_line.pk, self.manual_line.pk, self.root_line.pk, self.version_line.pk]
        self.manifest = {'billing.billingdocument': self.doc_ids, 'billing.billinglineitem': self.line_ids,
                         'inventory.inventorysale': [self.sale.pk]}
        self.classification = {'document_ids': self.doc_ids, 'reason': 'Owner explicitly classified these exact synthetic documents as sample.'}

    def document(self, number, **fields):
        fields.setdefault('document_type', 'invoice')
        # Match BillingService construction, which stamps this even on draft documents.
        fields.setdefault('issued_at', timezone.now())
        return BillingDocument.objects.create(tenant=self.org, organization=self.org, customer=self.customer,
            number=number, issue_date=timezone.localdate(), **fields)

    def line(self, document, **fields):
        line = BillingLineItem(tenant=self.org, organization=self.org,
            document=document, quantity=1, unit_price=100, **fields)
        if document.document_type == 'quotation':
            # Initial construction uses the same narrow flag as the owning quotation service.
            line._allow_document_edit = True
        line.save()
        return line

    def full_manifest(self):
        return {**self.manifest, 'products.product': [self.product.pk],
                'inventory.inventorybalance': [self.balance.pk], 'inventory.stockmovement': [self.movement.pk]}

    def plan(self, operation='sample_documents', **changes):
        options = dict(**self.common, manifest=self.full_manifest() if operation == 'classified_inventory' else self.manifest,
                       operation=operation, classification=self.classification)
        options.update(changes)
        return cleanup.make_plan(**options)

    def apply(self, plan):
        return cleanup.apply_plan(**self.common, plan=plan, fingerprint=plan['fingerprint'],
            confirmation=f"CLEANUP {self.org.slug} {plan['fingerprint']}", backup_reference='synthetic backup checksum',
            restore_reference='synthetic verified restore', maintenance_reference='isolated test database, writers stopped',
            reason='Synthetic maintenance regression')

    def snapshot(self, rows):
        for row in rows:
            row.refresh_from_db()
        return [cleanup.record(row) for row in rows]

    def preserved_rows(self):
        return [self.customer, self.profile, self.site, self.package, self.subscription,
                self.period, self.real_invoice, self.real_receipt, self.real_item, self.counter, self.old_audit]

    def test_explicit_classification_is_closed_and_default_inventory_stays_protected(self):
        default = cleanup.make_plan(**self.common, manifest=self.full_manifest())
        self.assertTrue(default['payload']['blockers'])
        for invalid in (None, {}, {'document_ids': self.doc_ids, 'reason': ''},
                        {'document_ids': self.doc_ids + [self.real_invoice.pk], 'reason': 'outside scope'},
                        {'document_ids': [True], 'reason': 'forged'},
                        {'document_ids': self.doc_ids, 'reason': 'x', 'force': True}):
            with self.assertRaises(cleanup.CleanupError):
                self.plan(classification=invalid)
        with self.assertRaises(cleanup.CleanupError):
            cleanup.make_plan(**self.common, manifest=self.full_manifest(), classification=self.classification)
        with self.assertRaises(cleanup.CleanupError):
            self.plan(manifest={**self.manifest, 'products.product': [self.product.pk]})
        TenantMembership.objects.filter(tenant=self.org, pk=self.membership.pk).update(base_role='SALES')
        with self.assertRaises(PermissionDenied):
            self.plan()

    def test_separate_policy_is_read_only_then_preserves_catalog_and_real_debt(self):
        self.assertIsNotNone(self.invoice.issued_at)
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan()
        self.assertEqual(plan['payload']['blockers'], [])
        self.assertTrue(all(q['sql'].lstrip().split()[0].upper() in ('SELECT', 'SHOW', 'BEGIN', 'COMMIT') for q in queries))
        if connection.vendor != 'postgresql':
            return
        protected = self.preserved_rows() + [self.product, self.balance, self.movement]
        before = self.snapshot(protected)
        balance_before = BillingService.customer_open_invoice_balance(organization=self.org, customer=self.customer)
        self.assertEqual(balance_before, Decimal('70.00'))
        self.apply(plan)
        self.assertEqual(self.snapshot(protected), before)
        self.assertEqual(BillingService.customer_open_invoice_balance(organization=self.org, customer=self.customer), balance_before)
        self.assertFalse(BillingDocument.objects.unscoped().filter(tenant=self.org, pk__in=self.doc_ids).exists())
        self.assertFalse(InventorySale.objects.unscoped().filter(tenant=self.org, pk=self.sale.pk).exists())
        event = AuditLog.objects.unscoped().get(tenant=self.org, action='handover.cleanup.applied')
        self.assertEqual(event.metadata['operation'], 'sample_documents')
        self.assertEqual(event.metadata['classification']['document_ids'], sorted(self.doc_ids))

    def test_classified_inventory_reaches_zero_products_preserving_real_history(self):
        if connection.vendor != 'postgresql':
            return
        plan = self.plan('classified_inventory')
        self.assertEqual(plan['payload']['blockers'], [])
        self.assertEqual(plan['payload']['postconditions'], {'products_remaining': 0})
        protected = self.preserved_rows()
        before = self.snapshot(protected)
        self.apply(plan)
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.org).count(), 0)
        self.assertEqual(self.snapshot(protected), before)
        self.assertEqual(BillingService.customer_open_invoice_balance(organization=self.org, customer=self.customer), Decimal('70.00'))
        self.assertEqual(AuditLog.objects.unscoped().get(tenant=self.org, action='handover.cleanup.applied').metadata['postconditions'], {'products_remaining': 0})

    def test_independent_new_product_invalidates_zero_catalog_plan_without_scope_expansion(self):
        if connection.vendor != 'postgresql':
            return
        plan = self.plan('classified_inventory')
        new_product = Product.objects.create(tenant=self.org, organization=self.org, name='New independent real product',
            sku='NEW', quantity=0, buying_price=10, selling_price=20)
        with self.assertRaisesMessage(cleanup.CleanupError, 'Stale'):
            self.apply(plan)
        blocked = self.plan('classified_inventory')
        self.assertTrue(any(b['id'] == new_product.pk and 'Zero-products target' in b['reason'] for b in blocked['payload']['blockers']))
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.org).count(), 2)

    def test_registered_paid_and_unpaid_sample_chains_remove_only_sample_receivables(self):
        if connection.vendor != 'postgresql':
            return
        operator = get_user_model().objects.create_user(username='classified-operator', password='pass')
        UserAccessProfile.objects.create(user=operator, tenant=self.org, role=UserAccessProfile.Role.TENANT_ADMIN)

        def issued():
            return BillingService.create_document(organization=self.org, created_by=operator,
                document_type='invoice', status='issued', customer_id=self.customer.pk,
                tax_rate=Decimal('0'), items=[LineItemInput(product_id=self.product.pk, quantity=Decimal('1'))])

        paid = issued()
        receipt = BillingService.create_receipt_from_invoice(organization=self.org, created_by=operator,
            invoice_id=paid.pk, amount_paid=paid.total, payment_method='cash', payment_reference='synthetic-classified-paid')
        unpaid = issued()
        doc_ids = [*self.doc_ids, paid.pk, receipt.pk, unpaid.pk]
        manifest = self.full_manifest()
        manifest['billing.billingdocument'] = doc_ids
        manifest['billing.billinglineitem'] = list(BillingLineItem.objects.unscoped().filter(
            tenant=self.org, document_id__in=doc_ids).values_list('pk', flat=True))
        sale_ids = list(InventorySale.objects.unscoped().filter(tenant=self.org, invoice_id__in=doc_ids).values_list('pk', flat=True))
        manifest['inventory.inventorysale'] = sale_ids
        manifest['inventory.inventorysaleline'] = list(InventorySaleLine.objects.unscoped().filter(
            tenant=self.org, sale_id__in=sale_ids).values_list('pk', flat=True))
        manifest['inventory.stockmovement'] = list(StockMovement.objects.unscoped().filter(
            tenant=self.org, product=self.product).values_list('pk', flat=True))
        plan = self.plan('classified_inventory', manifest=manifest,
            classification={'document_ids': doc_ids, 'reason': 'Exact synthetic posted and unpaid chains explicitly classified sample.'})
        self.assertEqual(plan['payload']['blockers'], [])
        self.assertEqual(BillingService.customer_open_invoice_balance(organization=self.org, customer=self.customer),
                         Decimal('70.00') + unpaid.total)
        protected = self.preserved_rows()
        before = self.snapshot(protected)
        self.apply(plan)
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.org).count(), 0)
        self.assertEqual(self.snapshot(protected), before)
        self.assertEqual(BillingService.customer_open_invoice_balance(organization=self.org, customer=self.customer), Decimal('70.00'))
        self.assertFalse(BillingDocument.objects.unscoped().filter(tenant=self.org, pk__in=doc_ids).exists())

    def test_posted_sales_cost_snapshots_and_real_subscription_links_block_separate_cleanup(self):
        InventorySale.objects.unscoped().filter(tenant=self.org, pk=self.sale.pk).update(stock_deducted=True)
        self.assertTrue(any('neutral sale' in b['reason'] for b in self.plan()['payload']['blockers']))
        InventorySale.objects.unscoped().filter(tenant=self.org, pk=self.sale.pk).update(stock_deducted=False)
        InventorySaleLine.objects.create(sale=self.sale, billing_line=self.product_line, cost_total=10, net_revenue=20)
        self.assertTrue(any(b['model'] == 'inventory.inventorysaleline' for b in self.plan()['payload']['blockers']))
        BillingLineItem.objects.unscoped().filter(tenant=self.org, pk=self.package_line.pk).update(subscription=self.subscription)
        self.assertTrue(any('Direct internet-service/subscription' in b['reason'] for b in self.plan('classified_inventory')['payload']['blockers']))

    def test_mixed_issued_billing_and_unclassified_receipts_or_periods_remain_blocked(self):
        BillingDocument.objects.unscoped().filter(tenant=self.org, pk=self.invoice.pk).update(status='issued')
        self.assertTrue(self.plan()['payload']['blockers'])
        self.assertTrue(any('mixed invoice' in b['reason'] for b in self.plan('classified_inventory')['payload']['blockers']))
        BillingDocument.objects.unscoped().filter(tenant=self.org, pk=self.invoice.pk).update(status='draft')
        receipt = self.document('UNCLASSIFIED-R', document_type='receipt', invoice=self.invoice, total=5)
        self.assertTrue(any(b['model'] == 'billing.billingdocument' and b['id'] == receipt.pk for b in self.plan()['payload']['blockers']))
        SubscriptionPeriod.objects.unscoped().filter(tenant=self.org, pk=self.period.pk).update(invoice=self.invoice)
        self.assertTrue(any(b['model'] == 'billing.subscriptionperiod' for b in self.plan('classified_inventory')['payload']['blockers']))

    def test_tampering_failure_rollback_and_replay_cannot_broaden_sample_scope(self):
        if connection.vendor != 'postgresql':
            return
        plan = self.plan()
        tampered = deepcopy(plan)
        tampered['payload']['classification']['document_ids'].append(self.real_invoice.pk)
        with self.assertRaises(cleanup.CleanupError):
            self.apply(tampered)
        original = cleanup._delete_exact

        def fail_after_delete(payload, tenant):
            original(payload, tenant)
            raise RuntimeError('Injected maintenance failure')

        with patch.object(cleanup, '_delete_exact', side_effect=fail_after_delete):
            with self.assertRaises(RuntimeError):
                self.apply(plan)
        self.assertEqual(BillingDocument.objects.unscoped().filter(tenant=self.org, pk__in=self.doc_ids).count(), 3)
        self.assertFalse(AuditLog.objects.unscoped().filter(tenant=self.org, action='handover.cleanup.applied').exists())
        self.assertTrue(AuditLog.objects.unscoped().filter(tenant=self.org, action='handover.cleanup.failed',
            metadata__operation='sample_documents').exists())
        self.apply(plan)
        with self.assertRaisesMessage(cleanup.CleanupError, 'Stale'):
            self.apply(plan)
        self.assertTrue(BillingDocument.objects.unscoped().filter(tenant=self.org, pk=self.real_invoice.pk).exists())

    def test_classified_cli_requires_fixed_evidence_and_refuses_operation_switch_on_apply(self):
        with TemporaryDirectory() as directory:
            manifest_path = Path(directory) / 'manifest.json'
            classification_path = Path(directory) / 'classification.json'
            output_path = Path(directory) / 'plan.json'
            manifest_path.write_text(json.dumps(self.manifest))
            classification_path.write_text(json.dumps(self.classification))
            call_command('handover_cleanup', tenant=self.org.slug, actor=self.actor.pk, support_session=self.support.pk,
                operation='sample_documents', manifest=str(manifest_path), classification_file=str(classification_path),
                output=str(output_path), stdout=StringIO())
            plan = json.loads(output_path.read_text())
            self.assertEqual(plan['payload']['operation'], 'sample_documents')
            with self.assertRaisesMessage(CommandError, 'operation does not match'):
                call_command('handover_cleanup', tenant=self.org.slug, actor=self.actor.pk, support_session=self.support.pk,
                    apply=True, operation='inventory', plan=str(output_path), fingerprint=plan['fingerprint'], stdout=StringIO())
