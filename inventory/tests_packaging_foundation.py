from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase

from audit.models import AuditLog
from billing.models import BillingDocument, BillingLineItem
from billing.services import BillingService, BillingServiceError, LineItemInput
from customers.models import Customer
from products.forms import ProductForm
from products.models import Product, ProductPackaging, UnitOfMeasure
from products.packaging import PackagingError, PackagingService, whole_quantity
from users.models import Organization, UserAccessProfile
from .models import (Cart, CartLine, InventoryBalance, PackagingActivation, Purchase, PurchaseLine,
                     StockContainer, StockMovement, Supplier)
from .packaging import PackagingActivationService
from .services import InventoryError, InventoryService


class PackagingFixture:
    def setUp(self):
        super().setUp()
        self.org = Organization.objects.create(name='Packaging', slug='packaging-test')
        self.other = Organization.objects.create(name='Other', slug='packaging-other')
        self.actor = get_user_model().objects.create_user(username='packaging-manager', password='pass')
        UserAccessProfile.objects.create(user=self.actor, tenant=self.org, role=UserAccessProfile.Role.TENANT_ADMIN)
        self.base = UnitOfMeasure.objects.create(tenant=self.org, organization=self.org, name='Length', symbol='m')
        self.box = UnitOfMeasure.objects.create(tenant=self.org, organization=self.org, name='Package', symbol='Box')
        self.product = Product.objects.create(tenant=self.org, organization=self.org, name='Synthetic cable', sku='PACK-1',
            sales_unit=self.base, quantity=0, stock=0, buying_price=Decimal('1'), selling_price=Decimal('3'))
        self.customer = Customer.objects.create(tenant=self.org, organization=self.org, name='Synthetic buyer')

    def configure(self, **overrides):
        values = dict(organization=self.org, actor=self.actor, product_id=self.product.pk, unit_id=self.box.pk,
                      factor='305', prices={'standard_price': '900.00', 'technician_price': '800.00', 'wholesale_price': '700.00'})
        values.update(overrides)
        return PackagingService.configure(**values)

    def stock(self, quantity, average='1'):
        Product.objects.unscoped().filter(pk=self.product.pk).update(quantity=quantity, stock=int(Decimal(quantity)))
        self.product.refresh_from_db()
        return InventoryBalance.objects.create(organization=self.org, tenant=self.org, product=self.product,
                                               quantity=Decimal(quantity), average_cost=Decimal(average))

    def change(self, **overrides):
        values = dict(organization=self.org, actor=self.actor, product_id=self.product.pk, enabled=True,
            request_key=str(uuid4()), reason='Verified synthetic physical count', expected_quantity='0',
            expected_average_cost='1', expected_version=Product.objects.unscoped().get(pk=self.product.pk).packaging_version, containers=[])
        values.update(overrides)
        return PackagingActivationService.change(**values)


class PackagingFoundationTests(PackagingFixture, TestCase):
    def test_existing_products_default_off_and_form_cannot_activate(self):
        self.assertFalse(self.product.packaging_enabled)
        self.assertEqual(self.product.packaging_version, 0)
        self.assertNotIn('packaging_enabled', ProductForm._meta.fields)
        self.product.packaging_enabled = True
        with self.assertRaises(ValidationError):
            self.product.save()
        self.product.refresh_from_db()
        self.assertFalse(self.product.packaging_enabled)

    def test_package_prices_are_independent_and_threshold_uses_base_quantity(self):
        self.product.allow_wholesale = True
        self.product.wholesale_min_quantity = Decimal('610')
        self.product.save()
        package = self.configure()
        self.assertEqual(package.price_for_sale_category(sale_pricing_category='standard', base_quantity=305), Decimal('900'))
        self.assertEqual(package.price_for_sale_category(sale_pricing_category='technician', base_quantity=305), Decimal('800'))
        self.assertEqual(package.price_for_sale_category(sale_pricing_category='wholesale', base_quantity=305), Decimal('900'))
        self.assertEqual(package.price_for_sale_category(sale_pricing_category='wholesale', base_quantity=610), Decimal('700'))
        self.product.refresh_from_db()
        self.assertFalse(self.product.packaging_enabled)
        self.assertEqual(self.product.packaging_version, 1)
        self.assertEqual(StockContainer.objects.count(), 0)

    def test_invalid_factors_fail_without_configuration_or_audit(self):
        before = AuditLog.objects.count()
        for factor in ('0', '-1', '0.5', '305.000001', 'NaN', 'Infinity', '10000000000', 'text'):
            with self.subTest(factor=factor), self.assertRaises(ValidationError):
                self.configure(factor=factor)
        self.assertEqual(ProductPackaging.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), before)
        self.product.refresh_from_db()
        self.assertEqual(self.product.packaging_version, 0)

    def test_strict_cost_floor_and_price_precision(self):
        for price in ('305.00', '304.99', '900.001', 'NaN'):
            with self.subTest(price=price), self.assertRaises(ValidationError):
                self.configure(prices={'standard_price': price})
        package = self.configure(prices={'standard_price': '305.01'})
        self.assertEqual(package.standard_price, Decimal('305.01'))

    def test_cost_floor_uses_movement_backed_weighted_average(self):
        self.stock('10', '4')
        StockMovement.objects.create(tenant=self.org, organization=self.org, product=self.product,
            movement_type='opening', quantity=10, balance_after=10, unit_cost=4)
        with self.assertRaises(ValidationError):
            self.configure(prices={'standard_price': '1000.00'})

    def test_permission_and_inactive_actor_denied(self):
        sales = get_user_model().objects.create_user(username='packaging-sales', password='pass')
        UserAccessProfile.objects.create(user=sales, tenant=self.org, role=UserAccessProfile.Role.TENANT_STAFF)
        with self.assertRaises(PermissionDenied):
            self.configure(actor=sales)
        self.actor.is_active = False
        self.actor.save()
        with self.assertRaises(PermissionDenied):
            self.configure()
        self.assertEqual(ProductPackaging.objects.count(), 0)

    def test_cross_tenant_product_and_unit_denied(self):
        foreign = UnitOfMeasure.objects.create(tenant=self.other, organization=self.other, name='Other box')
        with self.assertRaises(PermissionDenied):
            self.configure(unit_id=foreign.pk)
        with self.assertRaises(PermissionDenied):
            self.configure(organization=self.other)
        self.assertEqual(ProductPackaging.objects.count(), 0)

    def test_serialized_service_and_nonstock_combinations_denied(self):
        for attrs in ({'is_serialized': True}, {'item_type': 'service'}, {'track_stock': False}):
            Product.objects.unscoped().filter(pk=self.product.pk).update(item_type='physical', track_stock=True, is_serialized=False)
            Product.objects.unscoped().filter(pk=self.product.pk).update(**attrs)
            with self.subTest(attrs=attrs), self.assertRaises(ValidationError):
                self.configure()

    def test_factor_identity_is_immutable_and_revisions_do_not_rewrite_old_factor(self):
        first = self.configure()
        first.factor = Decimal('100')
        with self.assertRaises(ValidationError):
            first.save()
        second = self.configure(factor='100')
        first.refresh_from_db()
        self.assertEqual(first.factor, Decimal('305'))
        self.assertEqual(second.version, 2)
        self.product.refresh_from_db()
        self.product.sales_unit = self.box
        with self.assertRaises(ValidationError):
            self.product.save()

    def test_database_rejects_invalid_factor_without_model_validation(self):
        for factor in ('0', '-1', '1.5'):
            with self.subTest(factor=factor), self.assertRaises(IntegrityError), transaction.atomic():
                ProductPackaging.objects.bulk_create([ProductPackaging(tenant=self.org, product=self.product,
                    unit=self.box, version=1, factor=Decimal(factor), standard_price=900)])

    def test_activation_gate_is_closed_even_when_setting_is_supplied(self):
        self.configure()
        with self.settings(PACKAGING_WORKFLOWS_READY=True), self.assertRaisesMessage(ValidationError, 'unavailable until'):
            self.change()
        self.assertEqual(PackagingActivation.objects.count(), 0)
        self.assertEqual(StockContainer.objects.count(), 0)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_stocked_activation_maps_counts_without_quantity_value_or_movement_change(self, ready):
        package = self.configure()
        balance = self.stock('610', '1.123456')
        original = (balance.quantity, balance.average_cost, balance.total_value, self.product.quantity, self.product.stock)
        event = self.change(expected_quantity='610', expected_average_cost='1.123456', containers=[
            {'quantity': '305', 'packaging_id': package.pk, 'sealed': True},
            {'quantity': '305', 'packaging_id': package.pk, 'sealed': True}])
        balance.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual((balance.quantity, balance.average_cost, balance.total_value, self.product.quantity, self.product.stock), original)
        self.assertEqual(event.inventory_value, balance.total_value)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(list(event.containers.values_list('remaining_quantity', flat=True)), [Decimal('305'), Decimal('305')])
        self.assertTrue(self.product.packaging_enabled)
        self.assertEqual(AuditLog.objects.filter(action='inventory.packaging.activation_changed').count(), 1)
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            event.save()

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_zero_stock_activation_creates_no_physical_containers(self, ready):
        self.configure()
        self.change()
        self.assertEqual(StockContainer.objects.count(), 0)
        self.assertEqual(InventoryBalance.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_activation_retry_and_changed_content_conflict(self, ready):
        self.configure()
        key = str(uuid4())
        event = self.change(request_key=key, expected_version=1)
        self.assertEqual(self.change(request_key=key, expected_version=1).pk, event.pk)
        with self.assertRaises(ValidationError):
            self.change(request_key=key, expected_version=1, reason='Different content')
        self.assertEqual(PackagingActivation.objects.count(), 1)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_count_mismatch_never_multiplies_existing_stock(self, ready):
        package = self.configure()
        self.stock('10')
        with self.assertRaises(ValidationError):
            self.change(expected_quantity='10', containers=[{'quantity': '305', 'packaging_id': package.pk, 'sealed': True}])
        self.assertEqual(StockContainer.objects.count(), 0)
        self.assertEqual(PackagingActivation.objects.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('10'))

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_stale_value_or_cross_tenant_counts_roll_back(self, ready):
        self.configure()
        self.stock('10')
        with self.assertRaises(ValidationError):
            self.change(expected_quantity='9', containers=[{'quantity': '10'}])
        foreign_product = Product.objects.create(tenant=self.other, organization=self.other, name='Other cable', sku='OTHER-1',
            quantity=0, stock=0, buying_price=1, selling_price=3)
        foreign_unit = UnitOfMeasure.objects.create(tenant=self.other, organization=self.other, name='Foreign package')
        foreign_package = ProductPackaging.objects.create(tenant=self.other, product=foreign_product, unit=foreign_unit,
            factor=10, version=1, standard_price=30)
        with self.assertRaises(PermissionDenied):
            self.change(expected_quantity='10', containers=[{'quantity': '10', 'packaging_id': foreign_package.pk}])
        self.assertEqual(PackagingActivation.objects.count(), 0)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_open_cart_and_invoice_block_activation(self, ready):
        self.configure()
        cart = Cart.objects.create(tenant=self.org, organization=self.org, created_by=self.actor)
        CartLine.objects.create(tenant=self.org, cart=cart, product=self.product, quantity=1, unit_price=3)
        with self.assertRaises(ValidationError):
            self.change()
        cart.status = 'abandoned'; cart.save()
        BillingService.create_document(organization=self.org, created_by=self.actor, document_type='invoice',
            customer_id=self.customer.pk, issue_date=date.today(), items=[LineItemInput(product_id=self.product.pk, quantity=Decimal('1'))])
        with self.assertRaises(ValidationError):
            self.change()

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_disable_requires_zero_stock_and_zero_container_remainder(self, ready):
        package = self.configure(); self.stock('305')
        self.change(expected_quantity='305', containers=[{'quantity':'305', 'packaging_id':package.pk, 'sealed':True}])
        with self.assertRaises(ValidationError):
            self.change(enabled=False, expected_quantity='305')
        Product.objects.unscoped().filter(pk=self.product.pk).update(quantity=0, stock=0)
        InventoryBalance.objects.filter(product=self.product).update(quantity=0)
        with self.assertRaises(ValidationError):
            self.change(enabled=False)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_disable_zero_stock_retains_history(self, ready):
        self.configure(); self.change()
        self.change(enabled=False)
        self.product.refresh_from_db()
        self.assertFalse(self.product.packaging_enabled)
        self.assertEqual(PackagingActivation.objects.count(), 2)
        self.assertEqual(ProductPackaging.objects.count(), 1)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_container_cannot_be_resealed_or_change_provenance(self, ready):
        package = self.configure(); self.stock('100')
        event = self.change(expected_quantity='100', containers=[{'quantity':'100', 'packaging_id':package.pk}])
        container = event.containers.get()
        container.state = 'sealed'; container.remaining_quantity = 305; container.original_quantity = 305
        with self.assertRaises(ValidationError):
            container.save()
        container.refresh_from_db(); container.supplier_identifier = 'Rewrite'
        with self.assertRaises(ValidationError):
            container.save()

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_database_container_bounds_and_sealed_integrity(self, ready):
        package = self.configure(); self.stock('305')
        event = self.change(expected_quantity='305', containers=[{'quantity':'305', 'packaging_id':package.pk, 'sealed':True}])
        container = event.containers.get()
        for data in ({'remaining_quantity':306}, {'remaining_quantity':-1}, {'state':'unknown'}, {'remaining_quantity':100}):
            with self.subTest(data=data), self.assertRaises(IntegrityError), transaction.atomic():
                StockContainer.objects.filter(pk=container.pk).update(**data)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_expiry_count_is_required_and_preserved(self, ready):
        self.product.track_expiry=True; self.product.save()
        package=self.configure(); self.stock('305')
        row={'quantity':'305', 'packaging_id':package.pk, 'sealed':True}
        with self.assertRaises(ValidationError):
            self.change(expected_quantity='305', containers=[row])
        row['expiry_date']='2027-01-01'
        event=self.change(expected_quantity='305', containers=[row])
        self.assertEqual(event.containers.get().expiry_date, date(2027,1,1))

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_legacy_stock_and_billing_paths_reject_activated_product(self, ready):
        self.configure(); self.stock('10')
        self.change(expected_quantity='10', containers=[{'quantity':'10'}])
        with self.assertRaises(InventoryError):
            InventoryService.adjust_stock(organization=self.org, product_id=self.product.pk, quantity_delta=1,
                reason='manual_correction', actor=self.actor)
        with self.assertRaises(BillingServiceError):
            BillingService.create_document(organization=self.org, created_by=self.actor, document_type='invoice',
                customer_id=self.customer.pk, issue_date=date.today(), items=[LineItemInput(product_id=self.product.pk, quantity=Decimal('1'))])
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('10'))

    def test_incomplete_cart_and_purchase_snapshot_rejected_by_database(self):
        cart=Cart.objects.create(tenant=self.org, organization=self.org, created_by=self.actor)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CartLine.objects.bulk_create([CartLine(tenant=self.org, cart=cart, product=self.product, quantity=1,
                unit_price=3, unit_factor=305)])
        supplier=Supplier.objects.create(tenant=self.org, organization=self.org, company_name='Synthetic supplier', created_by=self.actor)
        purchase=Purchase.objects.create(tenant=self.org, organization=self.org, supplier=supplier, reference_number='PACK-TEST',
            purchase_date=date.today(), created_by=self.actor)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PurchaseLine.objects.bulk_create([PurchaseLine(tenant=self.org, purchase=purchase, product=self.product,
                quantity=305, unit_cost=1, unit_factor=305)])

    def test_packaging_is_product_specific_not_a_global_unit_factor(self):
        first=self.configure()
        other=Product.objects.create(tenant=self.org, organization=self.org, name='Different cable', sku='PACK-2',
            sales_unit=self.base, quantity=0, stock=0, buying_price=1, selling_price=3)
        second=self.configure(product_id=other.pk, factor='100', prices={'standard_price':'250.00'})
        self.assertEqual(first.unit_id,second.unit_id)
        self.assertEqual(first.factor,Decimal('305'))
        self.assertEqual(second.factor,Decimal('100'))

    def test_model_rejects_cross_tenant_definition_without_service(self):
        foreign=UnitOfMeasure.objects.create(tenant=self.other, organization=self.other, name='Other unit')
        with self.assertRaises(ValidationError):
            ProductPackaging.objects.create(tenant=self.org, product=self.product, unit=foreign, version=1, factor=305, standard_price=900)

    def test_maintenance_blocks_configured_product_dependency(self):
        from .handover_cleanup import resolve
        package=self.configure()
        plan=resolve({'products.product':[self.product.pk]},'inventory',self.org,self.actor)
        self.assertTrue(any(row['model']=='products.productpackaging' and row['id']==package.pk for row in plan['blockers']))
        self.assertEqual(ProductPackaging.objects.count(),1)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_stale_configuration_rejected_without_mutation(self, ready):
        self.configure(); self.configure(factor='100')
        with self.assertRaisesMessage(ValidationError,'configuration changed'):
            self.change(expected_version=1)
        self.assertEqual(PackagingActivation.objects.count(),0)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_activation_rolls_back_after_injected_container_write_failure(self, ready):
        package=self.configure(); self.stock('610')
        original=StockContainer.save
        calls=[]
        def fail_second(container,*args,**kwargs):
            calls.append(container)
            if len(calls)==2:
                raise RuntimeError('Synthetic failure')
            return original(container,*args,**kwargs)
        with patch.object(StockContainer,'save',fail_second), self.assertRaises(RuntimeError):
            self.change(expected_quantity='610',containers=[{'quantity':'305','packaging_id':package.pk,'sealed':True}]*2)
        self.assertEqual(StockContainer.objects.count(),0)
        self.assertEqual(PackagingActivation.objects.count(),0)
        self.product.refresh_from_db()
        self.assertFalse(self.product.packaging_enabled)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity,Decimal('610'))

    def test_activation_permission_checks_precede_gate(self):
        self.configure()
        with self.assertRaises(PermissionDenied):
            self.change(organization=self.other)
        self.assertEqual(PackagingActivation.objects.count(),0)

    def test_ended_support_session_cannot_configure_packaging(self):
        from users.models import SupportAccessSession,TenantMembership
        from django.utils import timezone
        super_actor=get_user_model().objects.create_superuser(username='pack-super',password='pass')
        TenantMembership.objects.create(tenant=self.other,user=super_actor,base_role=TenantMembership.BaseRole.SUPER_ADMIN)
        support=SupportAccessSession.objects.create(tenant=self.org,actor=super_actor,reason='Synthetic support',session_key='synthetic')
        package=self.configure(actor=super_actor,support_session=support)
        self.assertEqual(package.tenant_id,self.org.pk)
        support.ended_at=timezone.now();support.save()
        with self.assertRaises(PermissionDenied):
            self.configure(actor=super_actor,support_session=support)

    def test_sales_snapshot_database_checks_exact_conversion(self):
        from .models import CartLine
        package=self.configure()
        cart=Cart.objects.create(tenant=self.org,organization=self.org,created_by=self.actor)
        for base in (Decimal('609'),Decimal('611')):
            with self.subTest(base=base),self.assertRaises(IntegrityError),transaction.atomic():
                CartLine.objects.bulk_create([CartLine(tenant=self.org,cart=cart,product=self.product,quantity=2,unit_price=900,
                    packaging=package,packaging_version=1,unit_factor=305,base_quantity=base,selected_unit_snapshot='Box')])

    def test_non_integral_existing_stock_cannot_activate(self):
        self.configure()
        Product.objects.unscoped().filter(pk=self.product.pk).update(quantity=Decimal('1.5'),stock=1)
        with patch('products.packaging.workflows_ready',return_value=True),self.assertRaises(ValidationError):
            self.change(expected_quantity='1')
        self.assertEqual(StockContainer.objects.count(),0)

    def test_retirement_is_audited_idempotent_and_preserves_definition(self):
        package=self.configure()
        retired=PackagingService.retire(organization=self.org,actor=self.actor,product_id=self.product.pk,packaging_id=package.pk)
        self.assertFalse(retired.is_active)
        self.assertEqual(retired.factor,Decimal('305'))
        PackagingService.retire(organization=self.org,actor=self.actor,product_id=self.product.pk,packaging_id=package.pk)
        self.assertEqual(AuditLog.objects.filter(action='products.packaging.retired').count(),1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.packaging_version,2)

    def test_database_rejects_nonfinite_package_numbers(self):
        # Bypass DecimalField.to_python, which already rejects NaN, to exercise
        # the actual PostgreSQL CHECK constraints independently of Django.
        for factor, price, constraint in (('NaN','900','pack_factor_positive_whole'),
                                           ('305','NaN','pack_standard_nonnegative')):
            with self.subTest(factor=factor,price=price),self.assertRaisesRegex(IntegrityError,constraint),transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('INSERT INTO products_productpackaging '
                        '(tenant_id,product_id,unit_id,version,factor,standard_price,is_active,created_at) '
                        'VALUES (%s,%s,%s,1,%s,%s,true,CURRENT_TIMESTAMP)',
                        [self.org.pk,self.product.pk,self.box.pk,factor,price])

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_count_limits_and_forged_packaging_identifier_fail_without_mutation(self,ready):
        self.configure();self.stock('10')
        for row in ({'quantity':'10','packaging_id':{}},{'quantity':'10','sealed':'true'},{'quantity':'10','factor':'305'}, {'quantity':'0.5'}):
            with self.subTest(row=row),self.assertRaises(ValidationError):
                self.change(expected_quantity='10',containers=[row])
        self.assertEqual(PackagingActivation.objects.count(),0)
        self.assertEqual(StockContainer.objects.count(),0)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_partial_container_cannot_be_declared_sealed(self,ready):
        package=self.configure();self.stock('100')
        with self.assertRaises(ValidationError):
            self.change(expected_quantity='100',containers=[{'quantity':'100','packaging_id':package.pk,'sealed':True}])
        self.assertEqual(StockContainer.objects.count(),0)

    def test_excessive_expected_cost_is_an_actionable_validation_error(self):
        self.configure()
        for amount in ('NaN','Infinity','1E100000','1.0000001','-1'):
            with self.subTest(amount=amount),self.assertRaises(ValidationError):
                self.change(expected_average_cost=amount)
        self.assertEqual(PackagingActivation.objects.count(),0)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_posted_allocation_is_immutable_and_rejects_cross_tenant_relationships(self,ready):
        from .models import ContainerAllocation
        package=self.configure();self.stock('305')
        event=self.change(expected_quantity='305',containers=[{'quantity':'305','packaging_id':package.pk,'sealed':True}])
        document=BillingDocument.objects.create(tenant=self.org,organization=self.org,customer=self.customer,
            document_type='invoice',number='PACK-ALLOCATION',issue_date=date.today(),created_by=self.actor)
        line=BillingLineItem.objects.create(tenant=self.org,organization=self.org,document=document,product=self.product,
            quantity=1,unit_price=900,unit_snapshot='Box',packaging=package,packaging_version=event.version,unit_factor=305,base_quantity=305)
        movement=StockMovement.objects.create(tenant=self.org,organization=self.org,product=self.product,billing_line=line,
            movement_type='sale_out',quantity=-305,balance_after=0,unit_cost=1)
        allocation=ContainerAllocation.objects.create(tenant=self.org,organization=self.org,product=self.product,
            container=event.containers.get(),billing_line=line,movement=movement,base_quantity=305,unit_cost=1,cost_total=305)
        with self.assertRaises(ValidationError):
            allocation.save()
        with self.assertRaises(ValidationError):
            allocation.delete()
        with self.assertRaises(ValidationError):
            ContainerAllocation.objects.create(tenant=self.other,organization=self.other,product=self.product,
                container=event.containers.get(),billing_line=line,movement=movement,base_quantity=305,unit_cost=1,cost_total=305)
        self.assertEqual(ContainerAllocation.objects.count(),1)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_container_selection_is_stock_neutral_and_tenant_checked(self,ready):
        from .models import ProvisionalContainerAllocation
        package=self.configure();balance=self.stock('305')
        event=self.change(expected_quantity='305',containers=[{'quantity':'305','packaging_id':package.pk,'sealed':True}])
        cart=Cart.objects.create(tenant=self.org,organization=self.org,created_by=self.actor)
        line=CartLine.objects.create(tenant=self.org,cart=cart,product=self.product,quantity=1,unit_price=900,
            packaging=package,packaging_version=event.version,unit_factor=305,base_quantity=305,selected_unit_snapshot='Box')
        selection=ProvisionalContainerAllocation.objects.create(tenant=self.org,organization=self.org,
            cart_line=line,container=event.containers.get(),base_quantity=305)
        balance.refresh_from_db()
        self.assertEqual(balance.quantity,Decimal('305'))
        self.assertEqual(event.containers.get().remaining_quantity,Decimal('305'))
        self.assertEqual(StockMovement.objects.count(),0)
        with self.assertRaises(ValidationError):
            ProvisionalContainerAllocation.objects.create(tenant=self.other,organization=self.other,
                cart_line=line,container=event.containers.get(),base_quantity=1)
        self.assertEqual(ProvisionalContainerAllocation.objects.count(),1)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_repricing_preserves_physical_identity_and_posted_price_snapshot(self,ready):
        package=self.configure();self.stock('305')
        event=self.change(expected_quantity='305',containers=[{'quantity':'305','packaging_id':package.pk,'sealed':True}])
        document=BillingDocument.objects.create(tenant=self.org,organization=self.org,customer=self.customer,
            document_type='invoice',number='PACK-HISTORY',issue_date=date.today(),created_by=self.actor)
        line=BillingLineItem.objects.create(tenant=self.org,organization=self.org,document=document,product=self.product,
            quantity=1,unit_price=900,unit_snapshot='Box',packaging=package,packaging_version=event.version,unit_factor=305,base_quantity=305)
        BillingDocument.objects.filter(pk=document.pk).update(status='paid')
        PackagingService.update_prices(organization=self.org,actor=self.actor,product_id=self.product.pk,
            packaging_id=package.pk,prices={'standard_price':'950.00'})
        line.refresh_from_db();package.refresh_from_db();container=event.containers.get()
        self.assertEqual(line.unit_price,Decimal('900'))
        self.assertEqual(line.packaging_id,package.pk)
        self.assertEqual(package.standard_price,Decimal('950'))
        self.assertEqual(container.packaging_id,package.pk)
        self.assertEqual(container.capacity,Decimal('305'))
        self.assertEqual(container.state,'sealed')
        self.assertEqual(AuditLog.objects.filter(action='products.packaging.prices_updated').count(),1)

    def test_price_edit_service_preserves_strict_floor_and_direct_model_edits_fail(self):
        package=self.configure()
        package.standard_price=Decimal('950')
        with self.assertRaises(ValidationError):
            package.save()
        with self.assertRaises(ValidationError):
            PackagingService.update_prices(organization=self.org,actor=self.actor,product_id=self.product.pk,
                packaging_id=package.pk,prices={'standard_price':'305.00'})
        package.refresh_from_db()
        self.assertEqual(package.standard_price,Decimal('900'))

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_legacy_stock_without_audited_aggregate_balance_requires_preparation(self,ready):
        self.configure()
        Product.objects.unscoped().filter(pk=self.product.pk).update(quantity=10,stock=10)
        with self.assertRaisesMessage(ValidationError,'aggregate inventory balance'):
            self.change(expected_quantity='10',containers=[{'quantity':'10'}])
        self.assertEqual(InventoryBalance.objects.count(),0)
        self.assertEqual(StockContainer.objects.count(),0)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_database_whole_stock_rule_only_applies_to_activated_products(self,ready):
        Product.objects.unscoped().filter(pk=self.product.pk).update(quantity=Decimal('1.5'),stock=1)
        Product.objects.unscoped().filter(pk=self.product.pk).update(quantity=0,stock=0)
        self.configure();self.change()
        with self.assertRaises(IntegrityError),transaction.atomic():
            Product.objects.unscoped().filter(pk=self.product.pk).update(quantity=Decimal('1.5'),stock=1)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_completed_cart_selection_cannot_be_changed(self,ready):
        from .models import ProvisionalContainerAllocation
        package=self.configure();self.stock('305')
        event=self.change(expected_quantity='305',containers=[{'quantity':'305','packaging_id':package.pk,'sealed':True}])
        cart=Cart.objects.create(tenant=self.org,organization=self.org,created_by=self.actor)
        line=CartLine.objects.create(tenant=self.org,cart=cart,product=self.product,quantity=1,unit_price=900,
            packaging=package,packaging_version=event.version,unit_factor=305,base_quantity=305,selected_unit_snapshot='Box')
        selection=ProvisionalContainerAllocation.objects.create(tenant=self.org,organization=self.org,
            cart_line=line,container=event.containers.get(),base_quantity=305)
        Cart.objects.filter(pk=cart.pk).update(status='converted')
        line.refresh_from_db();selection.refresh_from_db()
        with self.assertRaises(ValidationError):
            selection.save()
        with self.assertRaises(ValidationError):
            selection.delete()


class PackagingConcurrencyTests(PackagingFixture, TransactionTestCase):
    def test_concurrent_configuration_allocates_distinct_versions(self):
        def configure(_):
            close_old_connections()
            try:
                org=Organization.objects.get(pk=self.org.pk)
                actor=get_user_model().objects.get(pk=self.actor.pk)
                return PackagingService.configure(organization=org, actor=actor, product_id=self.product.pk,
                    unit_id=self.box.pk, factor='305', prices={'standard_price':'900'}).version
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(configure, range(2)), [1,2])
        self.product.refresh_from_db()
        self.assertEqual(self.product.packaging_version, 2)

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_concurrent_activation_retry_creates_one_mapping(self, ready):
        package=self.configure(); self.stock('305'); key=str(uuid4())
        def activate(_):
            close_old_connections()
            try:
                org=Organization.objects.get(pk=self.org.pk)
                actor=get_user_model().objects.get(pk=self.actor.pk)
                return PackagingActivationService.change(organization=org, actor=actor, product_id=self.product.pk, enabled=True,
                    request_key=key, reason='Concurrent verified count', expected_quantity='305', expected_average_cost='1', expected_version=1,
                    containers=[{'quantity':'305','packaging_id':package.pk,'sealed':True}]).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(activate, range(2)))
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(StockContainer.objects.count(),1)
        self.assertEqual(PackagingActivation.objects.count(),1)
        self.assertEqual(StockMovement.objects.count(),0)
