from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from .models import InventoryBalance, Purchase, PurchaseLine, Supplier


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
        response = self.client.get(reverse('inventory:purchase_detail', args=[self.purchase.pk]))
        self.assertEqual(response.status_code, 200)
        self.purchase.refresh_from_db()
        balance = InventoryBalance.objects.get(product=self.product)
        self.assertEqual(self.purchase.status, Purchase.Status.DRAFT)
        self.assertEqual(balance.quantity, Decimal('10'))
        self.assertEqual(balance.average_cost, Decimal('100'))
