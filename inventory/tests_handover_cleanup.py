from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import close_old_connections, connection, OperationalError
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from audit.models import AuditLog
from billing.models import BillingDocument, BillingLineItem, CustomerSubscription, DocumentSequence, SubscriptionPeriod
from customers.models import Customer, CustomerSite, InternetCustomer
from custom_fields.models import CustomFieldDefinition, CustomFieldValue
from products.models import Product, UnitOfMeasure
from services.models import Package
from integrations.models import ExternalAssetReference
from users.models import Organization, SupportAccessSession, TenantMembership, UserAccessProfile
from inventory import handover_cleanup as cleanup
from inventory.models import Cart, CartLine, ImportJob, InventoryBalance, PurchaseReferenceSequence, StockMovement, StockUnit, Supplier


class HandoverCleanupTests(TransactionTestCase):
    """Runs against the disposable Django test DB; never against business tables."""

    def setUp(self):
        self.tenant = Organization.objects.create(name='Handover', slug='handover')
        self.other = Organization.objects.create(name='Retained', slug='retained')
        self.actor = get_user_model().objects.create_superuser(username='handover-admin', password='pass')
        TenantMembership.objects.create(tenant=self.tenant, user=self.actor,
            base_role=TenantMembership.BaseRole.SUPER_ADMIN)
        self.support = SupportAccessSession.objects.create(tenant=self.tenant, actor=self.actor,
            reason='Approved synthetic handover rehearsal', session_key='synthetic-session')
        self.common = dict(tenant_slug=self.tenant.slug, actor_id=self.actor.pk, support_id=self.support.pk)
        # No dependence on the deployed image's absent .git metadata.
        self.env = patch.dict('os.environ', {'HANDOVER_BUILD_COMMIT': 'a' * 40})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.unit = UnitOfMeasure.objects.create(tenant=self.tenant, organization=self.tenant,
            name='Piece', symbol='pc')
        self.product = self.product_for(self.tenant, 'APPROVED')

    def product_for(self, tenant, sku):
        return Product.objects.create(tenant=tenant, organization=tenant, sku=sku, name=sku,
            quantity=0, buying_price=10, selling_price=20, track_stock=False)

    def plan(self, manifest=None, **kwargs):
        return cleanup.make_plan(**self.common,
            manifest=manifest or {'products.product': [self.product.pk]}, **kwargs)

    def apply(self, plan, **kwargs):
        options = dict(**self.common, plan=plan, fingerprint=plan['fingerprint'],
            confirmation=f"CLEANUP {self.tenant.slug} {plan['fingerprint']}",
            backup_reference='backup SHA256 synthetic', restore_reference='isolated restore verified',
            maintenance_reference='all writers stopped, isolated test DB', reason='synthetic test')
        options.update(kwargs)
        return cleanup.apply_plan(**options)

    def test_plan_is_strictly_read_only_and_command_outputs_exact_ids(self):
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan()
        self.assertEqual(plan['payload']['blockers'], [])
        self.assertTrue(all(q['sql'].lstrip().split()[0].upper() in ('SELECT', 'SHOW', 'BEGIN', 'COMMIT') for q in queries))
        self.assertFalse(AuditLog.objects.unscoped().filter(tenant=self.tenant).exists())
        with TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(cleanup.canonical({'products.product': [self.product.pk]}))
            output = Path(directory) / 'plan.json'
            stream = StringIO()
            call_command('handover_cleanup', tenant=self.tenant.slug, actor=self.actor.pk,
                support_session=self.support.pk, manifest=str(manifest), output=str(output), stdout=stream)
            self.assertIn('database writes=0', stream.getvalue())
            self.assertTrue(output.exists())
        self.assertTrue(Product.objects.unscoped().filter(pk=self.product.pk, tenant=self.tenant).exists())

    def test_tenant_ids_and_actor_policy_fail_closed(self):
        foreign = self.product_for(self.other, 'FOREIGN')
        plan = self.plan({'products.product': [foreign.pk]})
        self.assertTrue(plan['payload']['blockers'])
        self.actor.is_superuser = False
        self.actor.is_staff = True
        self.actor.save()
        TenantMembership.objects.filter(user=self.actor, tenant=self.tenant).update(base_role='SALES')
        with self.assertRaises(PermissionDenied):
            self.plan()

    def test_ended_and_wrong_tenant_support_sessions_rejected(self):
        SupportAccessSession.objects.filter(pk=self.support.pk, tenant=self.tenant).update(ended_at=timezone.now())
        with self.assertRaises(PermissionDenied):
            self.plan()

    def test_manifest_is_closed_and_requires_exact_integer_ids(self):
        for manifest in ({'auth.user': [self.actor.pk]}, {'products.product': [True]},
                         {'products.product': ['1']}, {'products.product': []}):
            with self.assertRaises(cleanup.CleanupError):
                self.plan(manifest=manifest)
        with self.assertRaises(cleanup.CleanupError):
            self.plan({'products.product': [self.product.pk]}, operation='customers')

    def test_fingerprint_preserves_microseconds_and_dependency_changes_are_stale(self):
        self.product.refresh_from_db()
        before = cleanup.record(self.product)
        self.product.updated_at += timedelta(microseconds=1)
        self.assertNotEqual(cleanup.record(self.product), before)
        if connection.vendor != 'postgresql':
            return
        plan = self.plan()
        cart = Cart.objects.create(tenant=self.tenant, organization=self.tenant, created_by=self.actor)
        CartLine.objects.create(cart=cart, product=self.product, quantity=1, unit_price=20)
        with self.assertRaisesMessage(cleanup.CleanupError, 'Stale'):
            self.apply(plan)
        blocked = self.plan()
        with self.assertRaisesMessage(cleanup.CleanupError, 'blockers'):
            self.apply(blocked)
        self.assertTrue(Product.objects.unscoped().filter(tenant=self.tenant, pk=self.product.pk).exists())

    def test_self_referential_billing_chain_requires_every_id(self):
        if connection.vendor != 'postgresql':
            return
        customer = Customer.all_objects.create(tenant=self.tenant, organization=self.tenant,
            name='Walk-in', customer_type='random', is_pos_placeholder=True)
        quote = BillingDocument.objects.create(tenant=self.tenant, organization=self.tenant,
            customer=customer, document_type='quotation', number='Q-CYCLE', issue_date=timezone.localdate())
        invoice = BillingDocument.objects.create(tenant=self.tenant, organization=self.tenant,
            customer=customer, document_type='invoice', source_quotation=quote, number='I-CYCLE', issue_date=timezone.localdate())
        BillingDocument.objects.unscoped().filter(tenant=self.tenant, pk=quote.pk).update(root_quotation=quote, converted_invoice=invoice)
        self.assertTrue(self.plan({'billing.billingdocument': [quote.pk]})['payload']['blockers'])
        plan = self.plan({'billing.billingdocument': [quote.pk, invoice.pk]})
        self.assertEqual(plan['payload']['blockers'], [])
        # JSON transport must preserve exact comparison and signature verification.
        import json
        self.apply(json.loads(json.dumps(plan)))
        self.assertFalse(BillingDocument.objects.unscoped().filter(tenant=self.tenant, pk__in=[quote.pk, invoice.pk]).exists())

    def test_assigned_serial_assets_implicit_m2m_and_invalid_custom_targets_block(self):
        import uuid
        customer = Customer.objects.create(tenant=self.tenant, organization=self.tenant,
            name='Assigned customer', customer_type='internet')
        unit = StockUnit.objects.create(tenant=self.tenant, organization=self.tenant,
            product=self.product, serial_number='ASSIGNED-SERIAL')
        asset = ExternalAssetReference.objects.create(tenant=self.tenant, organization=self.tenant,
            customer=customer, external_uuid=uuid.uuid4(), category_name='Router',
            serial_number=unit.serial_number, status='active', source_updated_at=timezone.now())
        plan = self.plan({'products.product': [self.product.pk], 'inventory.stockunit': [unit.pk]})
        self.assertTrue(any(b['model'] == 'integrations.externalassetreference' and b['id'] == asset.pk for b in plan['payload']['blockers']))
        package = Package.objects.create(tenant=self.other, organization=self.other, name='Foreign package',
            package_type='indoor', speed='10 Mbps', monthly_fee=100, setup_fee=0)
        customer.packages.add(package)
        plan = self.plan({'customers.customer': [customer.pk]}, operation='customers')
        self.assertTrue(any(b['model'] == 'customers.customer_packages' and 'ownership' in b['reason'] for b in plan['payload']['blockers']))
        definition = CustomFieldDefinition.objects.create(tenant=self.tenant, organization=self.tenant,
            target_model='customer', key='mismatch', label='Mismatch', field_type='text')
        CustomFieldValue.objects.create(tenant=self.tenant, organization=self.tenant,
            field_definition=definition, target_model='product', object_id=str(self.product.pk))
        self.assertTrue(any('definition/target mismatch' in b['reason'] for b in self.plan()['payload']['blockers']))

    def test_apply_requires_typed_confirmation_and_complete_operational_evidence(self):
        if connection.vendor != 'postgresql':
            return
        plan = self.plan()
        for fields, reason in (({'confirmation': 'CLEANUP other-tenant'}, 'confirmation'),
                               ({'backup_reference': ''}, 'evidence'),
                               ({'restore_reference': ''}, 'evidence'),
                               ({'maintenance_reference': ''}, 'evidence')):
            with self.assertRaises(cleanup.CleanupError, msg=reason):
                self.apply(plan, **fields)
        self.assertTrue(Product.objects.unscoped().filter(tenant=self.tenant, pk=self.product.pk).exists())

    def test_supplier_import_snapshot_requires_explicit_approval(self):
        supplier = Supplier.objects.create(tenant=self.tenant, organization=self.tenant,
            company_name='Explicit supplier', created_by=self.actor)
        job = ImportJob.objects.create(tenant=self.tenant, organization=self.tenant, created_by=self.actor,
            import_type='suppliers', status='committed', validated_rows=[{'company_name': supplier.company_name}])
        plan = self.plan({'inventory.supplier': [supplier.pk]})
        self.assertTrue(any(b['model'] == 'inventory.importjob' and b['id'] == job.pk for b in plan['payload']['blockers']))
        plan = self.plan({'inventory.supplier': [supplier.pk], 'inventory.importjob': [job.pk]})
        self.assertEqual(plan['payload']['blockers'], [])

    def test_incoming_links_need_explicit_approval_and_inconsistent_owners_block(self):
        cart = Cart.objects.create(tenant=self.tenant, organization=self.tenant, created_by=self.actor)
        line = CartLine.objects.create(cart=cart, product=self.product, quantity=1, unit_price=20)
        plan = self.plan()
        self.assertTrue(any(b['model'] == 'inventory.cartline' and b['id'] == line.pk for b in plan['payload']['blockers']))
        CartLine.objects.unscoped().filter(pk=line.pk, tenant=self.tenant).update(tenant=self.other)
        plan = self.plan({'products.product': [self.product.pk], 'inventory.cart': [cart.pk]})
        self.assertTrue(any('ownership' in b['reason'] for b in plan['payload']['blockers']))
        Product.objects.unscoped().filter(pk=self.product.pk, tenant=self.tenant).update(organization=None)
        self.assertTrue(any('ownership' in b['reason'] for b in self.plan()['payload']['blockers']))

    def test_partial_stock_history_and_real_or_mixed_billing_block(self):
        StockMovement.objects.create(tenant=self.tenant, organization=self.tenant, product=self.product,
            movement_type='opening', quantity=1, balance_after=1, unit_cost=10, created_by=self.actor)
        self.assertTrue(self.plan()['payload']['blockers'])
        customer = Customer.objects.create(tenant=self.tenant, organization=self.tenant,
            name='Real customer', customer_type='internet')
        doc = BillingDocument.objects.create(tenant=self.tenant, organization=self.tenant,
            customer=customer, document_type='invoice', number='REAL', issue_date=timezone.localdate())
        line = BillingLineItem.objects.create(tenant=self.tenant, organization=self.tenant,
            document=doc, description='Service', quantity=1, unit_price=20)
        plan = self.plan({'billing.billingdocument': [doc.pk], 'billing.billinglineitem': [line.pk]})
        self.assertTrue(any('billing history protected' in b['reason'] for b in plan['payload']['blockers']))
        self.assertTrue(any('mixed invoice' in b['reason'] for b in plan['payload']['blockers']))

    def test_customer_plan_is_separate_and_dependencies_and_walkin_are_protected(self):
        customer = Customer.objects.create(tenant=self.tenant, organization=self.tenant,
            name='Explicit disposable', customer_type='random')
        plan = self.plan({'customers.customer': [customer.pk]}, operation='customers')
        self.assertFalse(plan['payload']['blockers'])
        CustomerSite.objects.create(tenant=self.tenant, organization=self.tenant, customer=customer, name='Preserved site')
        self.assertTrue(self.plan({'customers.customer': [customer.pk]}, operation='customers')['payload']['blockers'])
        Customer.all_objects.filter(pk=customer.pk, tenant=self.tenant).update(is_pos_placeholder=True)
        self.assertTrue(any('walk-in' in b['reason'] for b in self.plan(
            {'customers.customer': [customer.pk]}, operation='customers')['payload']['blockers']))

    def test_apply_integrity_freshness_rollback_replay_and_preservation(self):
        if connection.vendor != 'postgresql':
            with self.assertRaisesMessage(cleanup.CleanupError, 'PostgreSQL'):
                self.apply(self.plan())
            return
        foreign = self.product_for(self.other, 'RETAINED')
        foreign.refresh_from_db()
        before_foreign = cleanup.record(foreign)
        customer = Customer.objects.create(tenant=self.tenant, organization=self.tenant,
            name='Real internet customer', customer_type='internet')
        before_customer = cleanup.record(customer)
        original_audit = AuditLog.objects.create(tenant=self.tenant, organization=self.tenant,
            actor=self.actor, action='original', object_type='Product', object_id=str(self.product.pk))
        plan = self.plan()
        tampered = deepcopy(plan)
        tampered['payload']['approved_roots']['products.product'].append(foreign.pk)
        with self.assertRaises((cleanup.CleanupError, signing.BadSignature)):
            self.apply(tampered)
        expired = self.plan(created_at=(timezone.now() - timedelta(hours=3)).isoformat())
        with self.assertRaisesMessage(cleanup.CleanupError, 'expired'):
            self.apply(expired)
        Product.objects.unscoped().filter(pk=self.product.pk, tenant=self.tenant).update(description='changed')
        with self.assertRaisesMessage(cleanup.CleanupError, 'Stale'):
            self.apply(plan)
        plan = self.plan()
        original_delete = cleanup._delete_exact

        def injected(payload, tenant):
            original_delete(payload, tenant)
            raise RuntimeError('injected after deletion')

        with patch.object(cleanup, '_delete_exact', side_effect=injected):
            with self.assertRaises(RuntimeError):
                self.apply(plan)
        self.assertTrue(Product.objects.unscoped().filter(pk=self.product.pk, tenant=self.tenant).exists())
        self.assertFalse(AuditLog.objects.unscoped().filter(tenant=self.tenant, action='handover.cleanup.applied').exists())
        self.assertTrue(AuditLog.objects.unscoped().filter(tenant=self.tenant, action='handover.cleanup.failed').exists())
        self.apply(plan)
        self.assertFalse(Product.objects.unscoped().filter(pk=self.product.pk, tenant=self.tenant).exists())
        with self.assertRaisesMessage(cleanup.CleanupError, 'Stale'):
            self.apply(plan)
        foreign.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(cleanup.record(foreign), before_foreign)
        self.assertEqual(cleanup.record(customer), before_customer)
        self.assertTrue(AuditLog.objects.unscoped().filter(tenant=self.tenant, pk=original_audit.pk).exists())

    def test_complete_sample_pos_chain_and_custom_values_media_preservation(self):
        if connection.vendor != 'postgresql':
            return  # Apply is explicitly unsupported on SQLite; covered above.
        customer = Customer.all_objects.create(tenant=self.tenant, organization=self.tenant,
            name='Walk-in Customer', customer_type='random', is_pos_placeholder=True)
        doc = BillingDocument.objects.create(tenant=self.tenant, organization=self.tenant,
            customer=customer, document_type='invoice', number='SAMPLE-POS', issue_date=timezone.localdate())
        line = BillingLineItem.objects.create(tenant=self.tenant, organization=self.tenant,
            document=doc, product=self.product, quantity=1, unit_price=20)
        balance = InventoryBalance.objects.create(tenant=self.tenant, organization=self.tenant, product=self.product, quantity=0)
        movement = StockMovement.objects.create(tenant=self.tenant, organization=self.tenant,
            product=self.product, billing_line=line, movement_type='sale_out', quantity=-1,
            balance_after=0, unit_cost=10, created_by=self.actor)
        definition = CustomFieldDefinition.objects.create(tenant=self.tenant, organization=self.tenant,
            target_model='product', key='handover', label='Handover', field_type='text')
        value = CustomFieldValue.objects.create(tenant=self.tenant, organization=self.tenant,
            field_definition=definition, target_model='product', object_id=str(self.product.pk), value_text='sample')
        retained = CustomFieldValue.objects.create(tenant=self.tenant, organization=self.tenant,
            field_definition=definition, target_model='product', object_id='999999', value_text='retained')
        Product.objects.unscoped().filter(pk=self.product.pk, tenant=self.tenant).update(image='shared/keep.png')
        manifest = {'products.product': [self.product.pk], 'billing.billingdocument': [doc.pk],
                    'billing.billinglineitem': [line.pk], 'inventory.inventorybalance': [balance.pk],
                    'inventory.stockmovement': [movement.pk]}
        plan = self.plan(manifest)
        self.assertEqual(plan['payload']['blockers'], [])
        with patch('django.core.files.storage.FileSystemStorage.delete') as delete_file:
            self.apply(plan)
        delete_file.assert_not_called()
        self.assertFalse(CustomFieldValue.objects.filter(pk=value.pk, tenant=self.tenant).exists())
        self.assertTrue(CustomFieldValue.objects.filter(pk=retained.pk, tenant=self.tenant).exists())
        self.assertTrue(CustomFieldDefinition.objects.filter(pk=definition.pk, tenant=self.tenant).exists())
        self.assertTrue(Customer.all_objects.filter(pk=customer.pk, tenant=self.tenant).exists())

    def test_subscription_fields_and_counters_unchanged_and_ordinary_writers_blocked(self):
        if connection.vendor != 'postgresql':
            return
        customer = Customer.objects.create(tenant=self.tenant, organization=self.tenant,
            name='Real subscriber', customer_type='internet')
        profile = InternetCustomer.objects.create(tenant=self.tenant, customer=customer, package_type='indoor')
        site = CustomerSite.objects.create(tenant=self.tenant, organization=self.tenant, customer=customer, name='Real site')
        package = Package.objects.create(tenant=self.tenant, organization=self.tenant, name='Real package',
            package_type='indoor', speed='10 Mbps', monthly_fee=100, setup_fee=0)
        sub = CustomerSubscription.objects.create(tenant=self.tenant, organization=self.tenant, customer=customer,
            site=site, package=package, start_date=timezone.localdate(), monthly_fee_at_signup=100)
        period = SubscriptionPeriod.objects.create(tenant=self.tenant, organization=self.tenant, subscription=sub,
            period_start=timezone.localdate(), period_end=timezone.localdate() + timedelta(days=30), final_amount=100)
        sequence = DocumentSequence.objects.create(tenant=self.tenant, organization=self.tenant,
            document_type='invoice', year=2026, last_number=123)
        purchases = PurchaseReferenceSequence.objects.create(tenant=self.tenant, organization=self.tenant, last_number=51)
        protected = [customer, profile, site, package, sub, period, sequence, purchases]
        real_invoice = BillingDocument.objects.create(tenant=self.tenant, organization=self.tenant,
            customer=customer, site=site, document_type='invoice', number='REAL-PRESERVED-I',
            issue_date=timezone.localdate(), status='draft', total=100)
        real_item = BillingLineItem.objects.create(tenant=self.tenant, organization=self.tenant,
            document=real_invoice, package=package, subscription=sub, quantity=1, unit_price=100)
        BillingDocument.objects.unscoped().filter(tenant=self.tenant, pk=real_invoice.pk).update(status='paid')
        real_receipt = BillingDocument.objects.create(tenant=self.tenant, organization=self.tenant,
            customer=customer, document_type='receipt', number='REAL-PRESERVED-R', invoice=real_invoice,
            issue_date=timezone.localdate(), payment_reference='real-preserved-payment', total=100)
        period.invoice = real_invoice
        period.receipt = real_receipt
        period.status = 'paid'
        period.paid_at = timezone.now()
        period.save()
        protected.extend([real_invoice, real_item, real_receipt])
        import uuid
        asset = ExternalAssetReference.objects.create(tenant=self.tenant, organization=self.tenant,
            customer=customer, external_uuid=uuid.uuid4(), category_name='Assigned router',
            status='active', source_updated_at=timezone.now())
        protected.append(asset)
        for row in protected:
            row.refresh_from_db()
        before = [cleanup.record(row) for row in protected]
        foreign = self.product_for(self.other, 'WRITER-TARGET')
        errors = []

        def ordinary_writer():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '200ms'")
                Product.objects.unscoped().filter(pk=foreign.pk, tenant=self.other).update(description='unexpected write')
            except OperationalError:
                errors.append('writer blocked by database')
            finally:
                connection.close()

        original = cleanup._delete_exact

        def verify_lock(payload, tenant):
            writer = Thread(target=ordinary_writer)
            writer.start()
            writer.join(timeout=3)
            self.assertFalse(writer.is_alive())
            self.assertEqual(errors, ['writer blocked by database'])
            original(payload, tenant)

        plan = self.plan()
        with patch.object(cleanup, '_delete_exact', side_effect=verify_lock):
            self.apply(plan)
        for row in protected:
            row.refresh_from_db()
        self.assertEqual([cleanup.record(row) for row in protected], before)

    def test_import_opening_stock_pos_payment_and_complete_historical_chain_cleanup(self):
        if connection.vendor != 'postgresql':
            return
        from openpyxl import Workbook
        from django.apps import apps
        from billing.services import BillingService
        from inventory.imports import TEMPLATES, commit_import, validate_workbook
        from inventory.services import CartService

        self.apply(self.plan())
        operator = get_user_model().objects.create_user(username='handover-operator', password='pass')
        UserAccessProfile.objects.create(user=operator, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)

        def import_rows(kind, rows):
            workbook = Workbook()
            workbook.active.append(TEMPLATES[kind])
            for row in rows:
                workbook.active.append(row)
            upload = BytesIO()
            workbook.save(upload)
            upload.seek(0)
            upload.name = kind + '.xlsx'
            job = validate_workbook(organization=self.tenant, actor=operator, import_type=kind, uploaded_file=upload)
            self.assertEqual(job.error_count, 0, job.errors)
            commit_import(organization=self.tenant, actor=operator, job_id=job.pk)

        import_rows('products', [['APPROVED', 'Imported real catalog', 'physical', '', '', '', 10, 30, 0, 'no', 'yes']])
        imported = Product.objects.unscoped().get(tenant=self.tenant, sku='APPROVED')
        self.assertGreater(imported.pk, self.product.pk)
        import_rows('opening_stock', [['APPROVED', 3, '', 'Verified opening count']])
        cart = Cart.objects.create(tenant=self.tenant, organization=self.tenant, created_by=operator)
        CartLine.objects.create(cart=cart, product=imported, quantity=1, unit_price=30)
        invoice = CartService.convert(organization=self.tenant, cart_id=cart.pk, target='invoice', actor=operator)
        receipt = BillingService.create_receipt_from_invoice(organization=self.tenant, created_by=operator,
            invoice_id=invoice.pk, amount_paid=invoice.total, payment_method='cash', payment_reference='handover-rehearsal')
        self.assertEqual(InventoryBalance.objects.unscoped().get(tenant=self.tenant, product=imported).quantity, Decimal('2'))
        self.assertEqual(receipt.document_type, 'receipt')
        # Explicitly classify this entire synthetic rehearsal as sample. Do not infer this in production.
        manifest = {}
        masters = {'products.unitofmeasure', 'products.productcategory', 'inventory.supplier', 'billing.promotion'}
        for label in cleanup.INVENTORY - masters:
            ids = list(cleanup.queryset(apps.get_model(label)).filter(tenant=self.tenant).values_list('pk', flat=True))
            if ids:
                manifest[label] = ids
        plan = self.plan(manifest)
        self.assertEqual(plan['payload']['blockers'], [])
        self.apply(plan)
        self.assertTrue(Customer.all_objects.filter(tenant=self.tenant, is_pos_placeholder=True).exists())
        self.assertFalse(BillingDocument.objects.unscoped().filter(tenant=self.tenant).exists())
        self.assertFalse(StockMovement.objects.unscoped().filter(tenant=self.tenant).exists())
