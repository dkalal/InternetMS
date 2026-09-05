from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import BillingDocument
from customers.models import Customer
from products.models import Product
from users.models import Organization, TenantMembership, TenantPermissionGrant
from users.permissions import PermissionCode

from .models import Cart, CartLine
from .services import CartService, InventoryError


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

    def test_item_discount_cannot_bypass_missing_discount_permission(self):
        line = self.cart.lines.get()
        self.client.login(username='control-seller', password='pass')

        response = self.client.post(reverse('inventory:cart_line_edit', args=[self.cart.pk, line.pk]), {
            'product': self.product.pk,
            'quantity': '1.00',
            'discount_amount': '90.00',
        })

        self.assertRedirects(response, reverse('inventory:cart_detail', args=[self.cart.pk]))
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('0.00'))

    def test_limit_covers_combined_item_and_cart_discounts(self):
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_percent=Decimal('10.00'), scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        line = self.cart.lines.get()
        self.client.login(username='control-seller', password='pass')
        response = self.client.post(reverse('inventory:cart_line_edit', args=[self.cart.pk, line.pk]), {
            'product': self.product.pk,
            'quantity': '1.00',
            'discount_amount': '6.00',
        })
        self.assertRedirects(response, reverse('inventory:cart_detail', args=[self.cart.pk]))

        response = self.post_details(
            sale_pricing_category='customer_tier', discount_amount='5.00', tax_rate='0.00',
        )

        self.assertEqual(response.status_code, 200)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.discount_amount, Decimal('0.00'))
        self.assertEqual(self.cart.lines.get().discount_amount, Decimal('6.00'))

    def test_lower_percentage_or_fixed_cap_wins(self):
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_percent=Decimal('50.00'), max_discount_amount=Decimal('8.00'),
            scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        self.client.login(username='control-seller', password='pass')

        response = self.post_details(
            sale_pricing_category='customer_tier', discount_amount='9.00', tax_rate='0.00',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'TZS 8.00')
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.discount_amount, Decimal('0.00'))

    def test_conversion_rechecks_current_actor_limit_for_stale_cart(self):
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_percent=Decimal('10.00'), scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        # Represents a draft discounted earlier by a manager or an old write path.
        self.cart.discount_amount = Decimal('20.00')
        self.cart.save(update_fields=['discount_amount', 'updated_at'])

        with self.assertRaisesMessage(InventoryError, 'authorized limit of TZS 10.00'):
            CartService.convert(
                organization=self.organization,
                cart_id=self.cart.pk,
                target=BillingDocument.DocumentType.INVOICE,
                actor=self.seller,
            )

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.Status.DRAFT)
        self.assertIsNone(self.cart.invoice_id)

    def test_conversion_fails_closed_after_discount_grant_is_revoked(self):
        grant = TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_amount=Decimal('20.00'), scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        self.cart.discount_amount = Decimal('15.00')
        self.cart.save(update_fields=['discount_amount', 'updated_at'])
        grant.delete()

        with self.assertRaisesMessage(InventoryError, 'do not have permission'):
            CartService.convert(
                organization=self.organization,
                cart_id=self.cart.pk,
                target=BillingDocument.DocumentType.QUOTATION,
                actor=self.seller,
            )

    def test_checkout_rejects_unsaved_discount_above_fixed_cap_atomically(self):
        self.product.selling_price = Decimal('2000.00')
        self.product.save(update_fields=['selling_price'])
        line = self.cart.lines.get()
        line.quantity = Decimal('3.00')
        line.unit_price = Decimal('2000.00')
        line.save(update_fields=['quantity', 'unit_price', 'updated_at'])
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_amount=Decimal('1000.00'), scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        self.client.login(username='control-seller', password='pass')

        response = self.client.post(
            reverse('inventory:cart_convert', args=[self.cart.pk, BillingDocument.DocumentType.INVOICE]),
            {
                'customer': self.customer.pk,
                'walk_in_name': '',
                'sale_pricing_category': Cart.SalePricingCategory.CUSTOMER_TIER,
                'discount_amount': '2000.00',
                'tax_rate': '0.00',
                'notes': '',
            },
            follow=True,
        )

        self.assertContains(response, 'authorized limit of TZS 1,000.00')
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.Status.DRAFT)
        self.assertEqual(self.cart.discount_amount, Decimal('0.00'))
        self.assertFalse(BillingDocument.objects.filter(source_inventory_cart_invoice=self.cart).exists())

    def test_ajax_rejection_restores_authoritative_totals_and_explains_cap(self):
        self.product.selling_price = Decimal('2000.00')
        self.product.save(update_fields=['selling_price'])
        line = self.cart.lines.get()
        line.quantity = Decimal('3.00')
        line.unit_price = Decimal('2000.00')
        line.save(update_fields=['quantity', 'unit_price', 'updated_at'])
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_amount=Decimal('1000.00'), scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        self.client.login(username='control-seller', password='pass')

        response = self.client.post(reverse('inventory:cart_detail', args=[self.cart.pk]), {
            'customer': self.customer.pk,
            'walk_in_name': '',
            'sale_pricing_category': Cart.SalePricingCategory.CUSTOMER_TIER,
            'discount_amount': '2000.00',
            'tax_rate': '0.00',
            'notes': '',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest', HTTP_ACCEPT='application/json')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['discount'], '0.00')
        self.assertEqual(response.json()['grand_total'], '6000.00')
        self.assertIn('authorized limit of TZS 1,000.00', response.json()['message'])
        self.assertIn('data-pos-convert', response.json()['checkout_html'])

    def test_cart_ui_exposes_current_fixed_cap_as_native_input_boundary(self):
        self.product.selling_price = Decimal('2000.00')
        self.product.save(update_fields=['selling_price'])
        line = self.cart.lines.get()
        line.quantity = Decimal('3.00')
        line.unit_price = Decimal('2000.00')
        line.save(update_fields=['quantity', 'unit_price', 'updated_at'])
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_amount=Decimal('1000.00'), scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        self.client.login(username='control-seller', password='pass')

        response = self.client.get(reverse('inventory:cart_detail', args=[self.cart.pk]))

        self.assertContains(response, 'max="1000.00"', html=False)
        self.assertContains(response, 'up to TZS 1,000.00 remains here')

    def test_checkout_saves_valid_pending_discount_before_conversion(self):
        self.product.selling_price = Decimal('2000.00')
        self.product.save(update_fields=['selling_price'])
        line = self.cart.lines.get()
        line.quantity = Decimal('3.00')
        line.unit_price = Decimal('2000.00')
        line.save(update_fields=['quantity', 'unit_price', 'updated_at'])
        TenantPermissionGrant.objects.create(
            membership=self.seller_membership, granted_by=self.admin_membership,
            action_code=PermissionCode.CART_DISCOUNT_APPLY,
            max_discount_amount=Decimal('1000.00'), scope=TenantPermissionGrant.Scope.ASSIGNED,
        )
        self.client.login(username='control-seller', password='pass')

        response = self.client.post(
            reverse('inventory:cart_convert', args=[self.cart.pk, BillingDocument.DocumentType.INVOICE]),
            {
                'customer': self.customer.pk,
                'walk_in_name': '',
                'sale_pricing_category': Cart.SalePricingCategory.CUSTOMER_TIER,
                'discount_amount': '1000.00',
                'tax_rate': '0.00',
                'notes': '',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Cart.Status.CONVERTED)
        self.assertEqual(self.cart.discount_amount, Decimal('1000.00'))
        self.assertEqual(self.cart.invoice.discount_amount, Decimal('1000.00'))
        self.assertEqual(self.cart.invoice.total, Decimal('5000.00'))

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
