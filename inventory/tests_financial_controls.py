from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer
from products.models import Product
from users.models import Organization, TenantMembership, TenantPermissionGrant
from users.permissions import PermissionCode

from .models import Cart, CartLine


User = get_user_model()


class CartFinancialControlTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Controlled POS', slug='controlled-pos')
        self.admin = User.objects.create_user(username='control-admin', password='pass')
        self.seller = User.objects.create_user(username='control-seller', password='pass')
        self.admin_membership = TenantMembership.objects.create(
            tenant=self.organization, user=self.admin, base_role=TenantMembership.BaseRole.ADMIN_MANAGER,
        )
        self.seller_membership = TenantMembership.objects.create(
            tenant=self.organization, user=self.seller, base_role=TenantMembership.BaseRole.SALES,
        )
        self.customer = Customer.objects.create(
            organization=self.organization, tenant=self.organization, name='POS Customer',
            customer_type='random', pricing_tier=Customer.PricingTier.RETAIL,
        )
        self.product = Product.objects.create(
            organization=self.organization, tenant=self.organization, name='Service item', sku='CTRL-1',
            item_type=Product.ItemType.SERVICE, track_stock=False, buying_price=Decimal('50.00'),
            selling_price=Decimal('100.00'), technician_price=Decimal('80.00'), quantity=Decimal('0.00'),
        )
        self.cart = Cart.objects.create(
            organization=self.organization, tenant=self.organization, customer=self.customer, created_by=self.seller,
        )
        CartLine.objects.create(cart=self.cart, product=self.product, quantity=Decimal('1.00'), unit_price=Decimal('100.00'))

    def post_details(self, **overrides):
        payload = {
            'customer': self.customer.pk, 'walk_in_name': '', 'sale_pricing_category': 'technician',
            'discount_amount': '20.00', 'tax_rate': '18.00', 'notes': '',
        }
        payload.update(overrides)
        return self.client.post(reverse('inventory:cart_detail', args=[self.cart.pk]), payload)

    def test_ungranted_financial_fields_are_server_locked(self):
        self.client.login(username='control-seller', password='pass')
        response = self.post_details()
        self.assertEqual(response.status_code, 302)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.sale_pricing_category, Cart.SalePricingCategory.CUSTOMER_TIER)
        self.assertEqual(self.cart.discount_amount, Decimal('0.00'))
        self.assertEqual(self.cart.tax_rate, Decimal('0.00'))

    def test_discount_limit_is_enforced(self):
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_percent=Decimal('10.00'), scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        self.client.login(username='control-seller', password='pass')
        response = self.post_details(sale_pricing_category='customer_tier', tax_rate='0.00')
        self.assertEqual(response.status_code, 200)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.discount_amount, Decimal('0.00'))
        self.assertEqual(self.cart.tax_rate, Decimal('0.00'))

    def test_customer_category_allow_list_is_enforced_by_form(self):
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_PRICING_OVERRIDE,
            allowed_pricing_categories=['technician'], scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        self.client.login(username='control-seller', password='pass')
        response = self.post_details(sale_pricing_category='wholesale', discount_amount='0.00', tax_rate='0.00')
        self.assertEqual(response.status_code, 200)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.sale_pricing_category, Cart.SalePricingCategory.CUSTOMER_TIER)

        response = self.post_details(sale_pricing_category='technician', discount_amount='0.00', tax_rate='0.00')
        self.assertEqual(response.status_code, 302)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.sale_pricing_category, Cart.SalePricingCategory.TECHNICIAN)
        self.assertEqual(self.cart.lines.get().unit_price, Decimal('80.00'))

    def test_tax_rate_is_read_only_until_permission_is_granted(self):
        self.client.login(username='control-seller', password='pass')
        self.post_details(sale_pricing_category='customer_tier', discount_amount='0.00', tax_rate='7.50')
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.tax_rate, Decimal('0.00'))

        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_TAX_RATE_EDIT,
            scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        response = self.post_details(sale_pricing_category='customer_tier', discount_amount='0.00', tax_rate='7.50')
        self.assertEqual(response.status_code, 302)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.tax_rate, Decimal('7.50'))
