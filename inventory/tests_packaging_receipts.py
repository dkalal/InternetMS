from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from products.models import Product
from products.packaging import PackagingService
from .tests_packaging_foundation import PackagingFixture
from .models import Purchase, PurchaseLine, Supplier, StockContainer, StockMovement, InventoryBalance
from .forms import PurchaseLineForm
from .packaging_receipts import purchase_snapshot
from .services import InventoryService, InventoryError


class ReceiptFixture(PackagingFixture):
    def setUp(self):
        super().setUp()
        self.package=self.configure()
        with patch('products.packaging.workflows_ready',return_value=True):
            self.change()
        self.product.refresh_from_db()
        self.supplier=Supplier.objects.create(tenant=self.org,organization=self.org,company_name='Synthetic cable supplier',created_by=self.actor)
        self.purchase=Purchase.objects.create(tenant=self.org,organization=self.org,supplier=self.supplier,reference_number='BOX-RECEIPT',
            purchase_date=date.today(),created_by=self.actor)

    def line(self,quantity='10',price='305.00',package=True):
        values=purchase_snapshot(organization=self.org,product=self.product,packaging_id=self.package.pk if package else None,
            quantity=quantity,unit_cost=price,version=self.product.packaging_version)
        return PurchaseLine.objects.create(tenant=self.org,purchase=self.purchase,product=self.product,**values)

    def receive(self):
        return InventoryService.confirm_purchase(organization=self.org,purchase_id=self.purchase.pk,actor=self.actor)


@patch('products.packaging.workflows_ready',return_value=True)
class PackagingReceiptTests(ReceiptFixture,TestCase):
    def test_ten_boxes_receive_exactly_3050_base_units_and_ten_containers(self,ready):
        line=self.line()
        self.assertEqual(StockContainer.objects.count(),0)
        self.assertEqual(StockMovement.objects.count(),0)
        received=self.receive()
        balance=InventoryBalance.objects.get(product=self.product)
        self.assertEqual(balance.quantity,Decimal('3050'))
        self.assertEqual(balance.average_cost,Decimal('1'))
        self.assertEqual(received.total_cost,Decimal('3050'))
        self.assertEqual(line.containers.count(),10)
        self.assertEqual(set(line.containers.values_list('remaining_quantity',flat=True)),{Decimal('305')})
        self.assertEqual(set(line.containers.values_list('state',flat=True)),{'sealed'})
        self.assertEqual(StockMovement.objects.filter(purchase_line=line).count(),1)
        self.assertEqual(StockMovement.objects.get(purchase_line=line).quantity,Decimal('3050'))

    def test_receiving_retry_is_idempotent(self,ready):
        self.line();self.receive();self.receive()
        self.assertEqual(StockContainer.objects.count(),10)
        self.assertEqual(StockMovement.objects.count(),1)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity,Decimal('3050'))

    def test_base_receipt_is_one_open_piece_not_a_sealed_box(self,ready):
        self.line(quantity='305',price='1',package=False);self.receive()
        container=StockContainer.objects.get()
        self.assertEqual(container.state,'opened')
        self.assertIsNone(container.packaging_id)
        self.assertEqual(container.remaining_quantity,Decimal('305'))

    def test_independent_purchase_prices_preserve_payable_and_weighted_average(self,ready):
        self.line(quantity='1',price='100.00');self.receive()
        self.assertEqual(InventoryBalance.objects.get(product=self.product).average_cost,Decimal('0.327869'))
        self.assertEqual(self.purchase.lines.get().line_total,Decimal('100'))
        self.purchase=Purchase.objects.create(tenant=self.org,organization=self.org,supplier=self.supplier,reference_number='BOX-2',
            purchase_date=date.today(),created_by=self.actor)
        self.line(quantity='1',price='200.00');self.receive()
        self.assertEqual(InventoryBalance.objects.get(product=self.product).average_cost,Decimal('0.491803'))
        self.assertEqual(StockContainer.objects.count(),2)

    def test_multiple_packaging_sizes(self,ready):
        alternative=PackagingService.configure(organization=self.org,actor=self.actor,product_id=self.product.pk,unit_id=self.box.pk,
            factor='100',prices={'standard_price':'250'})
        self.product.refresh_from_db();self.line(quantity='2')
        values=purchase_snapshot(organization=self.org,product=self.product,packaging_id=alternative.pk,quantity='3',unit_cost='100',version=self.product.packaging_version)
        PurchaseLine.objects.create(tenant=self.org,purchase=self.purchase,product=self.product,**values)
        self.receive()
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity,Decimal('910'))
        self.assertEqual(StockContainer.objects.count(),5)

    def test_failure_after_stock_write_rolls_back_everything(self,ready):
        self.line()
        with patch('inventory.packaging_receipts.create_receipt_containers',side_effect=RuntimeError('Synthetic failure')),self.assertRaises(RuntimeError):
            self.receive()
        self.assertEqual(InventoryBalance.objects.count(),0)
        self.assertEqual(StockContainer.objects.count(),0)
        self.assertEqual(StockMovement.objects.count(),0)
        self.purchase.refresh_from_db();self.assertEqual(self.purchase.status,'draft')

    def test_stale_configuration_requires_explicit_review(self,ready):
        self.line()
        PackagingService.update_prices(organization=self.org,actor=self.actor,product_id=self.product.pk,packaging_id=self.package.pk,prices={'standard_price':'950'})
        with self.assertRaises(InventoryError): self.receive()
        self.assertEqual(StockContainer.objects.count(),0)

    def test_mixed_legacy_and_packaging_receipt_is_atomic(self,ready):
        self.line(quantity='1')
        ordinary=Product.objects.create(tenant=self.org,organization=self.org,name='Ordinary fittings',sku='NORMAL-PART',quantity=0,stock=0,buying_price=1,selling_price=3)
        PurchaseLine.objects.create(tenant=self.org,purchase=self.purchase,product=ordinary,quantity=Decimal('1.5'),unit_cost=Decimal('1.2'))
        self.receive()
        self.assertEqual(InventoryBalance.objects.get(product=ordinary).quantity,Decimal('1.5'))
        self.assertEqual(StockContainer.objects.count(),1)
        self.assertEqual(StockMovement.objects.count(),2)

    def test_cross_tenant_confirmation_rejected(self,ready):
        self.line()
        with self.assertRaises(PermissionDenied):
            InventoryService.confirm_purchase(organization=self.other,purchase_id=self.purchase.pk,actor=self.actor)
        self.assertEqual(StockMovement.objects.count(),0)

    def test_unit_aware_form_and_draft_are_stock_neutral(self,ready):
        data={'product':self.product.pk,'quantity':'10','unit_cost':'305','purchase_packaging':self.package.pk,
              'purchase_configuration':self.product.packaging_version}
        form=PurchaseLineForm(data,organization=self.org)
        self.assertTrue(form.is_valid(),form.errors)
        line=form.save(commit=False);line.purchase=self.purchase;line.save()
        self.assertEqual(line.quantity,Decimal('3050'))
        self.assertEqual(line.entered_quantity,Decimal('10'))
        self.assertEqual(line.unit_cost,Decimal('1'))
        self.assertEqual(StockMovement.objects.count(),0)
        reloaded=PurchaseLineForm(instance=line,organization=self.org)
        self.assertEqual(reloaded.initial['quantity'],Decimal('10'))
        self.assertEqual(reloaded.initial['unit_cost'],Decimal('305'))

    def test_forged_factor_and_partial_snapshot_cannot_post(self,ready):
        line=self.line()
        PurchaseLine.objects.filter(pk=line.pk).update(unit_cost=Decimal('999'))
        with self.assertRaises(InventoryError): self.receive()
        self.assertEqual(StockMovement.objects.count(),0)

    def test_import_rejects_packaged_product_explicitly(self,ready):
        from .purchase_import import validate_rows
        rows=validate_rows([{'sku':self.product.sku,'quantity':'10','unit_cost':'305'}],organization=self.org)
        self.assertFalse(rows[0]['valid'])
        self.assertIn('manual unit-aware',str(rows[0]['errors']))

    def test_purchase_review_shows_commercial_and_base_quantity(self,ready):
        self.line();self.client.force_login(self.actor)
        response=self.client.get(reverse('inventory:purchase_detail',args=[self.purchase.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'10 Box')
        self.assertContains(response,'3,050')

    def test_cached_draft_line_cannot_rewrite_confirmed_history(self,ready):
        line=self.line();self.receive();line.batch_reference='Rewrite'
        with self.assertRaises(ValidationError): line.save()
        self.assertEqual(PurchaseLine.objects.get(pk=line.pk).batch_reference,'')

    def test_receiving_and_completed_retry_require_authorized_actor(self,ready):
        from django.contrib.auth import get_user_model
        from users.models import UserAccessProfile
        sales=get_user_model().objects.create_user(username='receipt-sales',password='pass')
        UserAccessProfile.objects.create(user=sales,tenant=self.org,role=UserAccessProfile.Role.TENANT_STAFF)
        self.line()
        with self.assertRaises(PermissionDenied): InventoryService.confirm_purchase(organization=self.org,purchase_id=self.purchase.pk,actor=sales)
        self.receive()
        with self.assertRaises(PermissionDenied): InventoryService.confirm_purchase(organization=self.org,purchase_id=self.purchase.pk,actor=sales)
        self.assertEqual(StockContainer.objects.count(),10)

    def test_existing_container_balance_mismatch_blocks_receipt(self,ready):
        self.line(quantity='1');self.receive()
        InventoryBalance.objects.filter(product=self.product).update(quantity=Decimal('999'))
        self.purchase=Purchase.objects.create(tenant=self.org,organization=self.org,supplier=self.supplier,reference_number='MISMATCH',purchase_date=date.today(),created_by=self.actor)
        self.line(quantity='1')
        with self.assertRaises(InventoryError): self.receive()
        self.assertEqual(StockContainer.objects.count(),1)

    def test_packaging_cannot_disable_with_a_draft_receipt(self,ready):
        self.line()
        with self.assertRaises(ValidationError): self.change(enabled=False)

    def test_corrupt_foreign_packaging_unit_is_not_followed(self,ready):
        from products.models import UnitOfMeasure,ProductPackaging
        foreign=UnitOfMeasure.objects.create(tenant=self.other,organization=self.other,name='Foreign private unit')
        ProductPackaging.objects.filter(pk=self.package.pk).update(unit=foreign)
        with self.assertRaises(PermissionDenied): self.line()
        self.assertEqual(StockMovement.objects.count(),0)


class PackagingReceiptConcurrencyTests(ReceiptFixture,TransactionTestCase):
    @patch('products.packaging.workflows_ready',return_value=True)
    def test_simultaneous_receipt_retries_create_one_stock_posting(self,ready):
        self.line()
        def receive(_):
            close_old_connections()
            try:
                return self.receive().pk
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(receive,range(2)))
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(StockContainer.objects.count(),10)
        self.assertEqual(StockMovement.objects.count(),1)
