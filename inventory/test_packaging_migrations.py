"""Upgrade/rollback rehearsal on synthetic historical rows only."""
from datetime import date
from decimal import Decimal
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PackagingMigrationPreservationTests(TransactionTestCase):
    old_targets = [('products', '0018_productskucounter_and_more'), ('inventory', '0016_purchase_product_proposals'),
                   ('billing', '0033_walk_in_shipping_address')]
    @property
    def new_targets(self):
        return MigrationExecutor(connection).loader.graph.leaf_nodes()
    labels = [('products', 'Product'), ('inventory', 'Purchase'), ('inventory', 'PurchaseLine'),
              ('inventory', 'InventoryBalance'), ('inventory', 'StockMovement'),
              ('billing', 'BillingDocument'), ('billing', 'BillingLineItem')]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.old_targets)
        self.old_apps = executor.loader.project_state(self.old_targets).apps
        apps = self.old_apps
        org = apps.get_model('users', 'Organization').objects.create(name='Migration synthetic', slug='pack-migration')
        actor = apps.get_model('auth', 'User').objects.create(username='pack-migration')
        product = apps.get_model('products', 'Product').objects.create(tenant_id=org.pk, organization_id=org.pk,
            name='Historical cable', sku='PACK-MIGRATION', quantity=Decimal('7'), stock=7, measure_unit='Meter',
            buying_price=Decimal('500.123456'), selling_price=Decimal('900'),
            default_purchase_unit_label='Legacy box', default_purchase_conversion_factor=Decimal('305'),
            default_purchase_unit_cost=Decimal('100000'))
        supplier = apps.get_model('inventory', 'Supplier').objects.create(tenant_id=org.pk, organization_id=org.pk,
            company_name='Synthetic supplier', created_by_id=actor.pk)
        purchase = apps.get_model('inventory', 'Purchase').objects.create(tenant_id=org.pk, organization_id=org.pk,
            supplier_id=supplier.pk, reference_number='LEGACY-PACK', purchase_date=date(2026,1,1), status='confirmed',
            total_cost=Decimal('123.45'), created_by_id=actor.pk)
        line = apps.get_model('inventory', 'PurchaseLine').objects.create(tenant_id=org.pk, purchase_id=purchase.pk,
            product_id=product.pk, quantity=Decimal('7'), unit_cost=Decimal('17.635714'),
            source_purchase_unit_label='Legacy box', source_purchase_quantity=Decimal('2'), conversion_factor=Decimal('305'),
            source_purchase_unit_cost=Decimal('61.725'), authoritative_purchase_total=Decimal('123.45'))
        apps.get_model('inventory', 'InventoryBalance').objects.create(tenant_id=org.pk, organization_id=org.pk,
            product_id=product.pk, quantity=Decimal('7'), average_cost=Decimal('17.635714'))
        apps.get_model('inventory', 'StockMovement').objects.create(tenant_id=org.pk, organization_id=org.pk,
            product_id=product.pk, purchase_line_id=line.pk, movement_type='purchase_in', quantity=Decimal('7'),
            balance_after=Decimal('7'), unit_cost=Decimal('17.635714'))
        customer = apps.get_model('customers', 'Customer').objects.create(tenant_id=org.pk, organization_id=org.pk, name='Synthetic buyer')
        Document = apps.get_model('billing','BillingDocument')
        invoice = Document.objects.create(tenant_id=org.pk, organization_id=org.pk, customer_id=customer.pk,
            document_type='invoice', number='HIST-INVOICE', issue_date=date(2026,1,2), status='paid',
            subtotal=Decimal('50'), tax_amount=Decimal('9'), total=Decimal('59'))
        Document.objects.create(tenant_id=org.pk, organization_id=org.pk, customer_id=customer.pk, invoice_id=invoice.pk,
            document_type='receipt', number='HIST-RECEIPT', issue_date=date(2026,1,2), status='paid', total=Decimal('59'))
        apps.get_model('billing','BillingLineItem').objects.create(tenant_id=org.pk, organization_id=org.pk,
            document_id=invoice.pk, product_id=product.pk, quantity=Decimal('2'), unit_snapshot='Legacy Unit',
            base_unit_price=Decimal('25'), unit_price=Decimal('25'), line_total=Decimal('50'))
        self.fields = {label: [f.attname for f in apps.get_model(*label)._meta.concrete_fields] for label in self.labels}
        self.before = self.snapshot(apps)

    def snapshot(self, apps):
        return {label: list(apps.get_model(*label).objects.order_by('pk').values(*self.fields[label])) for label in self.labels}

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_additive_upgrade_and_unused_foundation_rollback_preserve_history_exactly(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.new_targets)
        apps = executor.loader.project_state(self.new_targets).apps
        self.assertEqual(self.snapshot(apps), self.before)
        self.assertFalse(apps.get_model('products','Product').objects.get(sku='PACK-MIGRATION').packaging_enabled)
        self.assertEqual(apps.get_model('inventory','StockContainer').objects.count(),0)
        self.assertEqual(apps.get_model('products','ProductPackaging').objects.count(),0)
        self.assertIsNone(apps.get_model('inventory','PurchaseLine').objects.get().unit_factor)
        self.assertIsNone(apps.get_model('billing','BillingLineItem').objects.get().base_quantity)
        executor = MigrationExecutor(connection)
        executor.migrate(self.old_targets)
        self.assertEqual(self.snapshot(executor.loader.project_state(self.old_targets).apps), self.before)
        executor = MigrationExecutor(connection)
        executor.migrate(self.new_targets)
        self.assertEqual(self.snapshot(executor.loader.project_state(self.new_targets).apps), self.before)

    def test_previous_model_can_insert_disabled_product_after_additive_upgrade(self):
        executor=MigrationExecutor(connection)
        executor.migrate(self.new_targets)
        apps=executor.loader.project_state(self.new_targets).apps
        old_product=self.old_apps.get_model('products','Product')
        original=old_product.objects.get(sku='PACK-MIGRATION')
        inserted=old_product.objects.create(tenant_id=original.tenant_id,organization_id=original.organization_id,
            name='Previous code writer',sku='PACK-OLD-WRITER',quantity=Decimal('1.5'),stock=1,buying_price=1,selling_price=3)
        updated=apps.get_model('products','Product').objects.get(pk=inserted.pk)
        self.assertFalse(updated.packaging_enabled)
        self.assertEqual(updated.packaging_version,0)
        self.assertEqual(updated.quantity,Decimal('1.5'))
        self.assertEqual(apps.get_model('inventory','StockContainer').objects.count(),0)
