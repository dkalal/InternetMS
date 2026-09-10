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
        MigrationExecutor(connection).migrate([self.migrate_to])
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
