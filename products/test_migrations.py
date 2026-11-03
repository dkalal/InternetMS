from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PurchasePackDefaultsMigrationTests(TransactionTestCase):
    migrate_from = ('products', '0016_product_image')
    migrate_to = ('products', '0017_product_default_purchase_conversion_factor_and_more')

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        Organization = old_apps.get_model('users', 'Organization')
        Product = old_apps.get_model('products', 'Product')
        tenant = Organization.objects.create(name='Legacy Tenant', slug='legacy-pack-tenant')
        self.product_id = Product.objects.create(
            organization_id=tenant.pk, tenant_id=tenant.pk, name='Legacy cable', sku='LEG-CABLE',
            quantity=Decimal('7.00'), measure_unit='Meter', buying_price=Decimal('500.00'),
            selling_price=Decimal('900.00'), stock=7,
        ).pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_legacy_product_is_backfilled_as_one_to_one(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        Product = apps.get_model('products', 'Product')
        product = Product.objects.get(pk=self.product_id)
        self.assertEqual(product.default_purchase_unit_label, 'Meter')
        self.assertEqual(product.default_purchase_conversion_factor, Decimal('1.000000'))
        self.assertEqual(product.default_purchase_unit_cost, Decimal('500.00'))
        self.assertEqual(product.buying_price, Decimal('500.000000'))


class SKUCounterMigrationTests(TransactionTestCase):
    old_target = ('products', '0017_product_default_purchase_conversion_factor_and_more')
    new_target = ('products', '0018_productskucounter_and_more')

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.old_target])
        self.apps = executor.loader.project_state([self.old_target]).apps
        self.tenant = self.apps.get_model('users', 'Organization').objects.create(name='Legacy SKU', slug='legacy-sku')

    def tearDown(self):
        # Clean deliberately invalid fixtures before restoring release schema.
        self.apps.get_model('products', 'Product').objects.filter(tenant_id=self.tenant.pk).delete()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def legacy(self, sku):
        return self.apps.get_model('products', 'Product').objects.create(tenant_id=self.tenant.pk,
            organization_id=self.tenant.pk, name='Legacy', sku=sku, quantity=0, buying_price=1, selling_price=2)

    def test_initialization_preserves_legacy_and_blank_codes(self):
        codes = [' prd-000123 ', 'PRD-42', 'PRD-400suffix', 'Old company code', '']
        for code in codes:
            self.legacy(code)
        executor = MigrationExecutor(connection)
        executor.migrate([self.new_target])
        apps = executor.loader.project_state([self.new_target]).apps
        self.assertEqual(apps.get_model('products', 'ProductSKUCounter').objects.get(tenant_id=self.tenant.pk).next_number, 124)
        self.assertCountEqual(apps.get_model('products', 'Product').objects.filter(tenant_id=self.tenant.pk).values_list('sku', flat=True), codes)

    def test_normalized_collision_blocks_without_renaming(self):
        self.legacy('Company')
        self.legacy(' company ')
        with self.assertRaisesRegex(RuntimeError, 'Resolve normalized duplicates explicitly'):
            MigrationExecutor(connection).migrate([self.new_target])
        self.assertEqual(self.apps.get_model('products', 'Product').objects.filter(tenant_id=self.tenant.pk).count(), 2)

    def test_out_of_range_generated_suffix_blocks(self):
        self.legacy('PRD-' + '9' * 40)
        with self.assertRaisesRegex(RuntimeError, 'out-of-range PRD suffix'):
            MigrationExecutor(connection).migrate([self.new_target])
