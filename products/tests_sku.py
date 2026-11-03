from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import perf_counter

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from products.creation import save_purchase_product
from products.models import Product, ProductSKUCounter, UnitOfMeasure
from products.sku import generated_number
from users.models import Organization, UserAccessProfile


class SKUAllocationTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='SKU', slug='sku')
        self.unit = UnitOfMeasure.objects.create(tenant=self.tenant, organization=self.tenant, name='Unit')

    def product(self, **values):
        defaults = dict(name='Router', quantity=0, buying_price=1, selling_price=2, sales_unit=self.unit)
        defaults.update(values)
        return Product(**defaults)

    def create(self, sku='', **values):
        return save_purchase_product(self.product(**values), organization=self.tenant, supplied_sku=sku)

    def test_sequence_services_tenants_and_growth(self):
        self.assertEqual(self.create().sku, 'PRD-000001')
        self.assertEqual(self.create(item_type='service').sku, 'PRD-000002')
        other = Organization.objects.create(name='Other SKU', slug='other-sku')
        self.assertEqual(save_purchase_product(Product(name='Service', quantity=0, buying_price=1, selling_price=2,
            item_type='service'), organization=other).sku, 'PRD-000001')
        ProductSKUCounter.objects.filter(tenant=self.tenant).update(next_number=1000000)
        self.assertEqual(self.create().sku, 'PRD-1000000')

    def test_initialization_and_stale_counter_skip_large_occupied_range(self):
        legacy = Product.objects.create(tenant=self.tenant, organization=self.tenant, sku='PRD-000041',
            name='Legacy', quantity=0, buying_price=1, selling_price=2, sales_unit=self.unit)
        Product.objects.unscoped().filter(pk=legacy.pk).update(sku=' prd-000041 ')
        self.assertEqual(self.create().sku, 'PRD-000042')
        ProductSKUCounter.objects.filter(tenant=self.tenant).update(next_number=41)
        self.assertEqual(self.create().sku, 'PRD-000043')

    def test_manual_codes_reserved_namespace_and_duplicates(self):
        self.assertEqual(self.create('  000a-01  ').sku, '000A-01')
        for code in ('000a-01', 'PRD-001', 'prd-0000001'):
            with self.assertRaises(ValidationError):
                self.create(code)

    def test_history_locks_explicit_sku_changes(self):
        product = self.create('COMPANY-OLD')
        Product.objects.unscoped().filter(pk=product.pk).update(quantity=1)
        product.refresh_from_db()
        product.sku = 'COMPANY-NEW'
        with self.assertRaises(ValidationError) as error:
            product.save()
        self.assertIn('sku', error.exception.message_dict)
        product.refresh_from_db()
        self.assertEqual(product.sku, 'COMPANY-OLD')

    def test_savepoint_recovery_preserves_existing_blank_identity(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        product = self.product(tenant=self.tenant, organization=self.tenant)
        product.save()
        original_pk = product.pk
        real_save = Product.save
        calls = []
        cause = Exception('synthetic PostgreSQL unique conflict')
        cause.diag = SimpleNamespace(constraint_name='uniq_product_sku_normalized')
        error = IntegrityError('unique conflict')
        error.__cause__ = cause
        def save(instance, *args, **kwargs):
            calls.append(instance.pk)
            if len(calls) == 1:
                raise error
            return real_save(instance, *args, **kwargs)
        with patch.object(Product, 'save', save):
            saved = save_purchase_product(product, organization=self.tenant, _backfill=True)
        self.assertEqual(saved.pk, original_pk)
        self.assertEqual(saved.sku, 'PRD-000002')
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.tenant).count(), 1)

    def test_validation_race_retries_only_sku_constraint_errors(self):
        from unittest.mock import patch
        real_save = Product.save
        calls = []
        def save(instance, *args, **kwargs):
            calls.append(instance.sku)
            if len(calls) == 1:
                raise ValidationError({'__all__': ValidationError('SKU conflict', code='sku_conflict')})
            return real_save(instance, *args, **kwargs)
        with patch.object(Product, 'save', save):
            self.assertEqual(self.create().sku, 'PRD-000002')
        with patch.object(Product, 'save', side_effect=ValidationError({'selling_price': 'Invalid price'})):
            with self.assertRaises(ValidationError) as error:
                self.create()
            self.assertIn('selling_price', error.exception.message_dict)

    def test_blank_update_rename_and_deletion(self):
        product = self.create()
        product.sku = ''
        product.name = 'Renamed'
        product.save()
        product.refresh_from_db()
        self.assertEqual(product.sku, 'PRD-000001')
        product.delete()
        self.assertEqual(self.create().sku, 'PRD-000002')

    def test_rollback_and_precise_validation_error(self):
        with self.assertRaises(ValidationError) as error:
            self.create(selling_price=-1)  # constructor values supplied below
        self.assertIn('selling_price', error.exception.message_dict)
        self.assertFalse(ProductSKUCounter.objects.filter(tenant=self.tenant).exists())
        with self.assertRaises(ValueError):
            with transaction.atomic():
                self.create()
                raise ValueError('rollback')
        self.assertEqual(self.create().sku, 'PRD-000001')

    def test_missing_and_foreign_tenant_and_unauthorized_actor(self):
        with self.assertRaises(ValidationError):
            save_purchase_product(self.product(), organization=None)
        other = Organization.objects.create(name='Other', slug='other')
        with self.assertRaises(PermissionDenied):
            save_purchase_product(self.product(tenant=other), organization=self.tenant)
        actor = get_user_model().objects.create_user(username='no-sku-rights')
        with self.assertRaises(PermissionDenied):
            save_purchase_product(self.product(), organization=self.tenant, actor=actor)

    def test_database_rejects_normalized_duplicate_even_with_bulk_write(self):
        self.create('CASE')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.bulk_create([self.product(tenant=self.tenant, organization=self.tenant, sku=' case ')])

    def test_suffix_parser_and_integer_boundary(self):
        for value in ('PRD-1x', 'XPRD-123', 'PRD-+1', 'PRD-١'):
            self.assertIsNone(generated_number(value))
        self.assertEqual(generated_number('PRD-0000001'), 1)
        with self.assertRaises(ValidationError):
            generated_number('PRD-' + '9' * 60)

    def test_backfill_is_dry_run_and_preserves_existing_codes(self):
        from io import StringIO
        from django.core.management import call_command
        product = self.product(tenant=self.tenant, organization=self.tenant)
        product.save()  # explicitly simulate a legacy blank record
        actor = get_user_model().objects.create_user(username='backfill-admin')
        UserAccessProfile.objects.create(user=actor, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)
        output = StringIO()
        call_command('audit_product_skus', tenant_id=self.tenant.pk, stdout=output)
        product.refresh_from_db()
        self.assertEqual(product.sku, '')
        self.assertFalse(ProductSKUCounter.objects.filter(tenant=self.tenant).exists())
        call_command('audit_product_skus', tenant_id=self.tenant.pk, actor_id=actor.pk, apply=True, stdout=output)
        product.refresh_from_db()
        self.assertEqual(product.sku, 'PRD-000001')
        call_command('audit_product_skus', tenant_id=self.tenant.pk, actor_id=actor.pk, apply=True, stdout=output)
        self.assertEqual(ProductSKUCounter.objects.get(tenant=self.tenant).next_number, 2)

    def test_200_allocations_have_no_repeated_catalog_scans(self):
        started = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            with transaction.atomic():
                for _ in range(200):
                    self.create()
        scans = [q for q in queries if 'LIKE' in q['sql'] and 'PRD-' in q['sql']]
        self.assertEqual(len(scans), 1)
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.tenant).count(), 200)
        print(f'SKU 200-product persistence baseline: {len(queries)} queries; {len(scans)} initialization scan; {perf_counter() - started:.3f}s')


class SKUCatalogImportTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='Import SKU', slug='import-sku')
        self.actor = get_user_model().objects.create_user(username='sku-importer')
        UserAccessProfile.objects.create(user=self.actor, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)

    def validate(self, codes):
        from io import BytesIO
        from openpyxl import Workbook
        from inventory.imports import TEMPLATES, validate_workbook
        workbook = Workbook()
        workbook.active.append(TEMPLATES['products'])
        for index, code in enumerate(codes):
            workbook.active.append([code, f'Import {index}', 'physical', '', '', '', 1, 2, 0, 'no', 'yes'])
        upload = BytesIO()
        workbook.save(upload)
        upload.seek(0)
        upload.name = 'sku-import.xlsx'
        return validate_workbook(organization=self.tenant, actor=self.actor, import_type='products', uploaded_file=upload)

    def test_preview_is_stock_neutral_and_commit_generates_only_blanks(self):
        from inventory.imports import commit_import
        from inventory.models import StockMovement
        job = self.validate(['', '000012', ''])
        self.assertEqual(job.error_count, 0, job.errors)
        self.assertFalse(Product.objects.unscoped().filter(tenant=self.tenant).exists())
        self.assertFalse(ProductSKUCounter.objects.filter(tenant=self.tenant).exists())
        commit_import(organization=self.tenant, actor=self.actor, job_id=job.pk)
        self.assertEqual(set(Product.objects.unscoped().filter(tenant=self.tenant).values_list('sku', flat=True)),
                         {'PRD-000001', '000012', 'PRD-000002'})
        commit_import(organization=self.tenant, actor=self.actor, job_id=job.pk)
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.tenant).count(), 3)
        self.assertFalse(StockMovement.objects.unscoped().filter(tenant=self.tenant).exists())

    def test_batch_duplicates_reserved_namespace_and_foreign_replay(self):
        from inventory.imports import commit_import
        self.assertGreater(self.validate(['same', 'SAME']).error_count, 0)
        self.assertGreater(self.validate(['PRD-000001']).error_count, 0)
        job = self.validate([''])
        other = get_user_model().objects.create_user(username='another-importer')
        UserAccessProfile.objects.create(user=other, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)
        with self.assertRaises(PermissionDenied):
            commit_import(organization=self.tenant, actor=other, job_id=job.pk)

    def test_non_sku_error_rolls_back_products_counter_and_import(self):
        from inventory.imports import commit_import
        from inventory.models import ImportJob
        job = self.validate(['', ''])
        job.validated_rows[1]['selling_price'] = '0'
        job.save(update_fields=['validated_rows'])
        with self.assertRaisesRegex(ValueError, 'Import row 3: Selling price'):
            commit_import(organization=self.tenant, actor=self.actor, job_id=job.pk)
        self.assertFalse(Product.objects.unscoped().filter(tenant=self.tenant).exists())
        self.assertFalse(ProductSKUCounter.objects.filter(tenant=self.tenant).exists())
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.VALIDATED)


class SKUConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='Race SKU', slug='race-sku')
        self.unit = UnitOfMeasure.objects.create(tenant=self.tenant, organization=self.tenant, name='Unit')

    def race(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locking and separate connections.')
        barrier = Barrier(3)
        def create(index):
            close_old_connections()
            try:
                tenant = Organization.objects.get(pk=self.tenant.pk)
                product = Product(name=f'Race {index}', quantity=0, buying_price=1, selling_price=2, sales_unit_id=self.unit.pk)
                barrier.wait(timeout=15)
                return save_purchase_product(product, organization=tenant).sku
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=3) as executor:
            codes = list(executor.map(create, range(3)))
        self.assertEqual(set(codes), {'PRD-000001', 'PRD-000002', 'PRD-000003'})
        self.assertEqual(ProductSKUCounter.objects.get(tenant=self.tenant).next_number, 4)

    def test_concurrent_first_counter_creation(self):
        self.race()

    def test_concurrent_counter_provisioning_without_parent_serialization(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locking.')
        from products.creation import locked_counter
        barrier = Barrier(3)
        def provision(index):
            close_old_connections()
            try:
                tenant = Organization.objects.get(pk=self.tenant.pk)
                barrier.wait(timeout=15)
                with transaction.atomic():
                    return locked_counter(tenant).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=3) as executor:
            self.assertEqual(list(executor.map(provision, range(3))), [self.tenant.pk] * 3)
        self.assertEqual(ProductSKUCounter.objects.filter(tenant=self.tenant).count(), 1)

    def test_concurrent_existing_counter_creation(self):
        ProductSKUCounter.objects.create(tenant=self.tenant)
        self.race()

    def test_concurrent_manual_batch_submission_replays(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locking.')
        import json
        from django.core import signing
        from django.test import Client
        from django.urls import reverse
        actor = get_user_model().objects.create_user(username='manual-batch-race')
        UserAccessProfile.objects.create(user=actor, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)
        token = signing.dumps({'tenant': self.tenant.pk, 'actor': actor.pk, 'key': 'manual-race'}, salt='product-batch')
        payload = {'batch_token': token, 'rows': json.dumps([{'id': 'r1', 'values': {
            'name': 'Manual batch', 'sku': 'COMPANY-1', 'item_type': 'service',
            'buying_price': '1', 'selling_price': '2', 'wholesale_min_quantity': '1',
            'category': 'other', 'is_active': True}}])}
        barrier = Barrier(2)
        def submit(index):
            close_old_connections()
            try:
                client = Client()
                client.force_login(get_user_model().objects.get(pk=actor.pk))
                barrier.wait(timeout=15)
                response = client.post(reverse('product-create'), payload)
                return response.status_code, response.json().get('replayed')
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, range(2)))
        self.assertCountEqual(results, [(200, False), (200, True)])
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.tenant).count(), 1)

    def test_concurrent_catalog_imports_and_replayed_commit(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locking.')
        from inventory.imports import commit_import
        from inventory.models import ImportJob
        actor = get_user_model().objects.create_user(username='import-race-admin')
        UserAccessProfile.objects.create(user=actor, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)
        jobs = []
        for index in range(2):
            jobs.append(ImportJob.objects.create(tenant=self.tenant, organization=self.tenant,
                created_by=actor, import_type='products', status=ImportJob.Status.VALIDATED,
                validated_rows=[dict(sku='', name=f'Imported {index}', item_type='physical', category='',
                    brand='', model_number='', buying_price='1', selling_price='2', reorder_threshold='0',
                    serialized=False, tax_eligible=True, _row=2)], row_count=1))
        barrier = Barrier(2)
        def commit(job_id):
            close_old_connections()
            try:
                tenant = Organization.objects.get(pk=self.tenant.pk)
                user = get_user_model().objects.get(pk=actor.pk)
                barrier.wait(timeout=15)
                return commit_import(organization=tenant, actor=user, job_id=job_id).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(set(executor.map(commit, [job.pk for job in jobs])), {job.pk for job in jobs})
        self.assertEqual(set(Product.objects.unscoped().filter(tenant=self.tenant).values_list('sku', flat=True)),
                         {'PRD-000001', 'PRD-000002'})
        commit_import(organization=self.tenant, actor=actor, job_id=jobs[0].pk)
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.tenant).count(), 2)
