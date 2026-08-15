from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Organization, TenantMembership, TenantPermissionGrant
from .permissions import PermissionCode


User = get_user_model()


class TeamFinancialControlGrantTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Access Controls', slug='access-controls')
        self.admin = User.objects.create_user(username='access-admin', password='pass')
        self.seller = User.objects.create_user(username='access-seller', password='pass')
        self.admin_membership = TenantMembership.objects.create(tenant=self.organization, user=self.admin, base_role=TenantMembership.BaseRole.ADMIN_MANAGER)
        self.seller_membership = TenantMembership.objects.create(tenant=self.organization, user=self.seller, base_role=TenantMembership.BaseRole.SALES)

    def test_manager_can_set_cart_permissions_and_limits_without_new_role(self):
        self.client.login(username='access-admin', password='pass')
        response = self.client.post(reverse('update_member_access', args=[self.seller_membership.pk]), {
            'base_role': TenantMembership.BaseRole.SALES,
            'scope': TenantPermissionGrant.Scope.ASSIGNED,
            'permissions': [PermissionCode.CART_PRICING_OVERRIDE, PermissionCode.CART_DISCOUNT_APPLY, PermissionCode.CART_TAX_RATE_EDIT],
            'allowed_pricing_categories': ['technician', 'wholesale'],
            'max_discount_percent': '5.00',
            'max_discount_amount': '50000.00',
        })
        self.assertRedirects(response, reverse('team_access'))
        self.seller_membership.refresh_from_db()
        self.assertEqual(self.seller_membership.base_role, TenantMembership.BaseRole.SALES)
        discount = self.seller_membership.permission_grants.get(action_code=PermissionCode.CART_DISCOUNT_APPLY)
        pricing = self.seller_membership.permission_grants.get(action_code=PermissionCode.CART_PRICING_OVERRIDE)
        self.assertEqual(discount.max_discount_percent, Decimal('5.00'))
        self.assertEqual(discount.max_discount_amount, Decimal('50000.00'))
        self.assertEqual(pricing.allowed_pricing_categories, ['technician', 'wholesale'])
        self.assertTrue(self.seller_membership.permission_grants.filter(action_code=PermissionCode.CART_TAX_RATE_EDIT).exists())
