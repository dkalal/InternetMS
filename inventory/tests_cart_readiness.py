from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from customers.models import Customer
from products.models import Product, ProductCategory, UnitOfMeasure
from products.pricing import cost_floor_for
from users.models import Organization, UserAccessProfile

from .models import Cart, CartLine, InventoryBalance, StockMovement


User = get_user_model()


class CartReadinessTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='Cart Readiness Tenant', slug='cart-readiness')
        self.admin = User.objects.create_user(username='cart-readiness-admin', password='pass')
        UserAccessProfile.objects.create(user=self.admin, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)
        unit = UnitOfMeasure.objects.create(organization=self.tenant, tenant=self.tenant, name='Piece', symbol='pc')
        category = ProductCategory.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Routers', default_unit=unit,
        )
        self.product = Product.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Unsafe Router', sku='UNSAFE-RTR',
            catalog_category=category, sales_unit=unit, quantity=Decimal('0'), stock=0,
            buying_price=Decimal('50'), selling_price=Decimal('100'), technician_price=Decimal('90'),
            track_stock=True,
        )
        InventoryBalance.objects.create(
            organization=self.tenant, tenant=self.tenant, product=self.product,
            quantity=Decimal('5'), average_cost=Decimal('100'),
        )
        StockMovement.objects.create(
            organization=self.tenant, tenant=self.tenant, product=self.product,
            movement_type=StockMovement.MovementType.OPENING, quantity=Decimal('5'),
            balance_after=Decimal('5'), unit_cost=Decimal('100'), created_by=self.admin,
        )
        self.cart = Cart.objects.create(
            organization=self.tenant, tenant=self.tenant, created_by=self.admin,
            sale_pricing_category=Cart.SalePricingCategory.STANDARD,
        )
        self.client.login(username='cart-readiness-admin', password='pass')

    def catalog_product(self, response, product=None):
        product = product or self.product
        return next(item for item in response.context['catalog'] if item.pk == product.pk)

    def get_catalog_product(self, product=None):
        response = self.client.get(reverse('inventory:cart_detail', args=[self.cart.pk]))
        return response, self.catalog_product(response, product)

    def test_unsafe_product_remains_visible_but_has_no_add_action(self):
        response = self.client.get(reverse('inventory:cart_detail', args=[self.cart.pk]))
        product = next(item for item in response.context['catalog'] if item.pk == self.product.pk)
        self.assertFalse(product.pos_ready)
        self.assertContains(response, 'Pricing review required before this product can be added to a sale.')
        self.assertContains(response, '>Pricing review</button>', html=False)

    def test_safe_price_restores_catalog_action(self):
        self.product.selling_price = Decimal('150')
        self.product.save()
        response = self.client.get(reverse('inventory:cart_detail', args=[self.cart.pk]))
        product = next(item for item in response.context['catalog'] if item.pk == self.product.pk)
        self.assertTrue(product.pos_ready)
        self.assertContains(response, 'data-pos-adjust')

    def test_below_equal_and_above_cost_are_classified_strictly(self):
        for price, expected in (
            (Decimal('99.99'), False), (Decimal('100.00'), False), (Decimal('100.01'), True),
        ):
            self.product.selling_price = price
            self.product.save(update_fields=['selling_price'])
            _, product = self.get_catalog_product()
            self.assertEqual(product.pos_ready, expected, price)
            self.assertEqual(product.pos_ready, product.pos_price > cost_floor_for(self.product))

    def test_readiness_uses_active_cart_pricing_mode(self):
        self.product.selling_price = Decimal('150')
        self.product.save()
        self.cart.sale_pricing_category = Cart.SalePricingCategory.TECHNICIAN
        self.cart.save()
        response = self.client.get(reverse('inventory:cart_detail', args=[self.cart.pk]))
        product = next(item for item in response.context['catalog'] if item.pk == self.product.pk)
        self.assertFalse(product.pos_ready)
        self.assertEqual(product.pos_price, Decimal('90.00'))

    def test_explicit_wholesale_and_customer_tier_modes_use_effective_price(self):
        self.product.selling_price = Decimal('150')
        self.product.allow_wholesale = True
        self.product.wholesale_price = Decimal('80')
        self.product.wholesale_min_quantity = Decimal('1')
        self.product.save(update_fields=[
            'selling_price', 'allow_wholesale', 'wholesale_price', 'wholesale_min_quantity',
        ])
        self.cart.sale_pricing_category = Cart.SalePricingCategory.WHOLESALE
        self.cart.save(update_fields=['sale_pricing_category'])
        _, product = self.get_catalog_product()
        self.assertFalse(product.pos_ready)
        self.assertEqual(product.pos_pricing_mode, 'wholesale')

        customer = Customer.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Tier Customer',
            customer_type='random', location='Moshi', pricing_tier=Customer.PricingTier.TECHNICIAN,
        )
        self.cart.customer = customer
        self.cart.sale_pricing_category = Cart.SalePricingCategory.CUSTOMER_TIER
        self.cart.save(update_fields=['customer', 'sale_pricing_category'])
        _, product = self.get_catalog_product()
        self.assertFalse(product.pos_ready)
        self.assertEqual(product.pos_price, Decimal('90.00'))
        self.assertEqual(product.pos_pricing_mode, 'technician')

    def test_legacy_retail_uses_next_quantity_at_wholesale_threshold(self):
        self.cart.sale_pricing_category = Cart.SalePricingCategory.LEGACY_RETAIL
        self.cart.save(update_fields=['sale_pricing_category'])
        self.product.selling_price = Decimal('150')
        self.product.allow_wholesale = True
        self.product.wholesale_price = Decimal('80')
        self.product.wholesale_min_quantity = Decimal('3')
        self.product.save(update_fields=[
            'selling_price', 'allow_wholesale', 'wholesale_price', 'wholesale_min_quantity',
        ])
        _, product = self.get_catalog_product()
        self.assertTrue(product.pos_ready)
        self.assertEqual(product.pos_pricing_mode, 'retail')
        CartLine.objects.create(
            cart=self.cart, product=self.product, quantity=Decimal('2'), unit_price=Decimal('150'),
        )
        _, product = self.get_catalog_product()
        self.assertFalse(product.pos_ready)
        self.assertEqual(product.pos_price, Decimal('80.00'))
        self.assertEqual(product.pos_pricing_mode, 'wholesale')

    def test_floor_fallbacks_match_authoritative_helper(self):
        StockMovement.objects.filter(product=self.product).delete()
        self.product.selling_price = Decimal('60')
        self.product.save(update_fields=['selling_price'])
        _, product = self.get_catalog_product()
        self.assertEqual(cost_floor_for(self.product), Decimal('50.000000'))
        self.assertTrue(product.pos_ready)

        StockMovement.objects.create(
            organization=self.tenant, tenant=self.tenant, product=self.product,
            movement_type=StockMovement.MovementType.OPENING, quantity=Decimal('0'),
            balance_after=Decimal('0'), unit_cost=Decimal('100'), created_by=self.admin,
        )
        InventoryBalance.objects.filter(product=self.product).update(quantity=Decimal('0'))
        _, product = self.get_catalog_product()
        self.assertEqual(cost_floor_for(self.product), Decimal('50.000000'))
        self.assertTrue(product.pos_ready)

    def test_unsafe_serialized_product_has_no_serial_selection_link(self):
        self.product.is_serialized = True
        self.product.save(update_fields=['is_serialized'])
        response, product = self.get_catalog_product()
        self.assertFalse(product.pos_ready)
        self.assertNotContains(
            response, reverse('inventory:cart_line_create', args=[self.cart.pk]) + f'?product={self.product.pk}',
        )

    def test_service_readiness_uses_buying_price_fallback(self):
        service = Product.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Installation', sku='SERVICE-1',
            catalog_category=self.product.catalog_category, sales_unit=self.product.sales_unit,
            item_type=Product.ItemType.SERVICE, track_stock=False,
            buying_price=Decimal('40'), selling_price=Decimal('60'), quantity=Decimal('0'), stock=0,
        )
        response, product = self.get_catalog_product(service)
        self.assertTrue(product.pos_ready)
        self.assertEqual(cost_floor_for(service), Decimal('40.000000'))
        self.assertContains(response, f'value="{service.pk}"', html=False)

    def test_crafted_post_is_still_rejected_server_side(self):
        response = self.client.post(reverse('inventory:cart_line_adjust', args=[self.cart.pk]), {
            'product': self.product.pk,
            'direction': 'add',
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CartLine.objects.filter(cart=self.cart).exists())

    def test_crafted_cart_line_form_is_rejected_server_side(self):
        response = self.client.post(reverse('inventory:cart_line_create', args=[self.cart.pk]), {
            'product': self.product.pk, 'quantity': '1', 'discount_amount': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'must be greater than the current cost')
        self.assertFalse(CartLine.objects.filter(cart=self.cart).exists())

    def test_cross_tenant_product_is_absent_and_cannot_be_added(self):
        other = Organization.objects.create(name='Other Tenant', slug='other-cart-ready')
        unit = UnitOfMeasure.objects.create(organization=other, tenant=other, name='Piece', symbol='pc')
        category = ProductCategory.objects.create(
            organization=other, tenant=other, name='Other', default_unit=unit,
        )
        foreign = Product.objects.create(
            organization=other, tenant=other, name='Foreign', sku='FOREIGN',
            catalog_category=category, sales_unit=unit, buying_price=Decimal('1'),
            selling_price=Decimal('2'), track_stock=True, quantity=Decimal('0'), stock=0,
        )
        response = self.client.get(reverse('inventory:cart_detail', args=[self.cart.pk]))
        self.assertNotIn(foreign.pk, [item.pk for item in response.context['catalog']])
        response = self.client.post(reverse('inventory:cart_line_adjust', args=[self.cart.pk]), {
            'product': foreign.pk, 'direction': 'add',
        })
        self.assertEqual(response.status_code, 404)

    def test_catalog_query_count_is_bounded_as_products_grow(self):
        url = reverse('inventory:cart_detail', args=[self.cart.pk])
        with CaptureQueriesContext(connection) as baseline_queries:
            self.client.get(url)
        unit = self.product.sales_unit
        category = self.product.catalog_category
        Product.objects.bulk_create([
            Product(
                organization=self.tenant, tenant=self.tenant, name=f'Router {index:03d}',
                sku=f'ROUTER-{index:03d}', catalog_category=category, sales_unit=unit,
                buying_price=Decimal('50'), selling_price=Decimal('150'), track_stock=True,
                quantity=Decimal('0'), stock=0,
            )
            for index in range(30)
        ])
        with CaptureQueriesContext(connection) as grown_queries:
            self.client.get(url)
        self.assertLessEqual(len(grown_queries), len(baseline_queries) + 1)
