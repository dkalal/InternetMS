from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from .models import InventoryBalance, Purchase, PurchaseLine, StockMovement, Supplier
from .services import InventoryService


User = get_user_model()


class PurchaseReviewReadinessTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='Review Tenant', slug='review-tenant')
        self.admin = User.objects.create_user(username='review-admin', password='pass')
        UserAccessProfile.objects.create(user=self.admin, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)
        self.unit = UnitOfMeasure.objects.create(organization=self.tenant, tenant=self.tenant, name='Piece', symbol='pc')
        self.category = ProductCategory.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Routers', default_unit=self.unit,
        )
        self.product = Product.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Router', sku='REVIEW-RTR',
            catalog_category=self.category, sales_unit=self.unit, quantity=Decimal('0'), stock=0,
            buying_price=Decimal('100'), selling_price=Decimal('150'), track_stock=True,
        )
        self.supplier = Supplier.objects.create(
            organization=self.tenant, tenant=self.tenant, company_name='Review Supplier', created_by=self.admin,
        )
        self.purchase = Purchase.objects.create(
            organization=self.tenant, tenant=self.tenant, supplier=self.supplier,
            reference_number='REVIEW-001', purchase_date=date.today(), created_by=self.admin,
        )
        PurchaseLine.objects.create(purchase=self.purchase, product=self.product, quantity=Decimal('5'), unit_cost=Decimal('200'))
        PurchaseLine.objects.create(purchase=self.purchase, product=self.product, quantity=Decimal('5'), unit_cost=Decimal('300'), batch_reference='B2')
        InventoryBalance.objects.create(
            organization=self.tenant, tenant=self.tenant, product=self.product,
            quantity=Decimal('10'), average_cost=Decimal('100'),
        )
        self.client.login(username='review-admin', password='pass')

    def make_product(self, *, name, sku, **updates):
        values = {
            'organization': self.tenant,
            'tenant': self.tenant,
            'name': name,
            'sku': sku,
            'catalog_category': self.category,
            'sales_unit': self.unit,
            'quantity': Decimal('0'),
            'stock': 0,
            'buying_price': Decimal('100'),
            'selling_price': Decimal('500'),
            'track_stock': True,
        }
        values.update(updates)
        return Product.objects.create(**values)

    def test_duplicate_product_lines_are_aggregated_into_one_projected_warning(self):
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['product_type_count'], 1)
        self.assertEqual(len(response.context['pricing_reviews']), 1)
        review = response.context['pricing_reviews'][0]
        self.assertEqual(review['projected_floor'], Decimal('175.000000'))
        self.assertIn('Standard', review['categories'])
        self.assertContains(response, '1 required')

    def test_safe_price_is_reported_ready(self):
        self.product.selling_price = Decimal('200')
        self.product.save()
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.context['pricing_reviews'], [])
        self.assertContains(response, 'No projected cost-floor pricing conflicts were found.')

    def test_review_does_not_mutate_purchase_stock_or_balance(self):
        movement_count = StockMovement.objects.filter(tenant=self.tenant).count()
        original_status = self.purchase.status
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.status_code, 200)
        self.purchase.refresh_from_db()
        balance = InventoryBalance.objects.get(product=self.product)
        self.assertEqual(self.purchase.status, original_status)
        self.assertEqual(balance.quantity, Decimal('10'))
        self.assertEqual(balance.average_cost, Decimal('100'))
        self.assertEqual(StockMovement.objects.filter(tenant=self.tenant).count(), movement_count)

    def test_empty_purchase_review_is_ready_and_has_zero_counts(self):
        self.purchase.lines.all().delete()
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['product_type_count'], 0)
        self.assertEqual(response.context['pricing_reviews'], [])
        self.assertContains(response, 'No projected cost-floor pricing conflicts were found.')

    def test_missing_and_zero_quantity_balances_use_only_incoming_cost(self):
        for balance_mode in ('missing', 'zero'):
            with self.subTest(balance_mode=balance_mode):
                InventoryBalance.objects.filter(product=self.product).delete()
                if balance_mode == 'zero':
                    InventoryBalance.objects.create(
                        organization=self.tenant, tenant=self.tenant, product=self.product,
                        quantity=Decimal('0'), average_cost=Decimal('999'),
                    )
                response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
                self.assertEqual(response.context['pricing_reviews'][0]['projected_floor'], Decimal('250.000000'))

    def test_multiple_products_and_tracking_counts_are_derived_from_lines(self):
        serialized = self.make_product(name='Serialized AP', sku='REVIEW-SERIAL', is_serialized=True)
        expiring = self.make_product(name='Expiring Battery', sku='REVIEW-EXP', track_expiry=True)
        PurchaseLine.objects.create(
            purchase=self.purchase, product=serialized, quantity=Decimal('2'), unit_cost=Decimal('50'),
            serial_numbers='SERIAL-001\nSERIAL-002', batch_reference='SERIAL-BATCH',
        )
        PurchaseLine.objects.create(
            purchase=self.purchase, product=expiring, quantity=Decimal('3'), unit_cost=Decimal('40'),
            batch_reference='EXP-BATCH', expiry_date=date(2030, 1, 1),
        )
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.context['product_type_count'], 3)
        self.assertEqual(len(response.context['purchase_lines']), 4)
        self.assertEqual(response.context['serialized_line_count'], 1)
        self.assertEqual(response.context['expiry_line_count'], 1)

    def test_equal_prices_warn_for_every_enabled_pricing_mode_only(self):
        self.product.selling_price = Decimal('175')
        self.product.technician_price = Decimal('175')
        self.product.wholesale_price = Decimal('175')
        self.product.allow_wholesale = True
        self.product.save(update_fields=[
            'selling_price', 'technician_price', 'wholesale_price', 'allow_wholesale',
        ])
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.context['pricing_reviews'][0]['categories'], [
            'Standard', 'Technician', 'Wholesale',
        ])

        self.product.allow_wholesale = False
        self.product.save(update_fields=['allow_wholesale'])
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.context['pricing_reviews'][0]['categories'], ['Standard', 'Technician'])

    def test_confirmed_purchase_uses_actual_inventory_cost_floor(self):
        self.product.selling_price = Decimal('175')
        self.product.technician_price = Decimal('176')
        self.product.save(update_fields=['selling_price', 'technician_price'])
        InventoryService.confirm_purchase(
            organization=self.tenant, purchase_id=self.purchase.pk, actor=self.admin,
        )
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        review = response.context['pricing_reviews'][0]
        self.assertEqual(review['projected_floor'], Decimal('175.000000'))
        self.assertEqual(review['categories'], ['Standard'])
        self.assertContains(response, 'Actual cost-floor pricing review')

    def test_user_without_cost_permission_sees_status_without_cost_amount(self):
        staff = User.objects.create_user(username='review-staff', password='pass')
        UserAccessProfile.objects.create(
            user=staff, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_STAFF,
        )
        self.client.logout()
        self.client.login(username='review-staff', password='pass')
        with patch('inventory.views._scope', return_value=self.tenant):
            response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 required')
        self.assertNotContains(response, 'projected cost floor')
        self.assertNotContains(response, '175 TZS')

    def test_cross_tenant_purchase_cannot_be_reviewed(self):
        other = Organization.objects.create(name='Other Review Tenant', slug='other-review-tenant')
        outsider = User.objects.create_user(username='review-outsider', password='pass')
        UserAccessProfile.objects.create(
            user=outsider, tenant=other, role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.client.logout()
        self.client.login(username='review-outsider', password='pass')
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.status_code, 404)
