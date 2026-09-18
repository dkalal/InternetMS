from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from products.models import Product, ProductCategory, UnitOfMeasure
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

    def test_readiness_uses_active_cart_pricing_mode(self):
        self.product.selling_price = Decimal('150')
        self.product.save()
        self.cart.sale_pricing_category = Cart.SalePricingCategory.TECHNICIAN
        self.cart.save()
        response = self.client.get(reverse('inventory:cart_detail', args=[self.cart.pk]))
        product = next(item for item in response.context['catalog'] if item.pk == self.product.pk)
        self.assertFalse(product.pos_ready)
        self.assertEqual(product.pos_price, Decimal('90.00'))

    def test_crafted_post_is_still_rejected_server_side(self):
        response = self.client.post(reverse('inventory:cart_line_adjust', args=[self.cart.pk]), {
            'product': self.product.pk,
            'direction': 'add',
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CartLine.objects.filter(cart=self.cart).exists())
