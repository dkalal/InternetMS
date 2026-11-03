from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from .models import InventoryBalance, Purchase, PurchaseLine, StockMovement, StockUnit, Supplier
from .services import InventoryService


User = get_user_model()


class UnifiedPurchaseWorkspaceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Workspace Tenant', slug='workspace-tenant')
        self.other_organization = Organization.objects.create(name='Other Tenant', slug='other-workspace-tenant')
        self.admin = User.objects.create_user(username='workspace-admin', password='pass')
        UserAccessProfile.objects.create(
            user=self.admin, tenant=self.organization, role=UserAccessProfile.Role.TENANT_ADMIN
        )
        self.sales = User.objects.create_user(username='workspace-sales', password='pass')
        UserAccessProfile.objects.create(
            user=self.sales, tenant=self.organization, role=UserAccessProfile.Role.TENANT_STAFF
        )
        self.unit = UnitOfMeasure.objects.create(
            organization=self.organization, tenant=self.organization, name='Unit', symbol='pc'
        )
        self.category = ProductCategory.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Equipment',
            default_unit=self.unit,
            measure_unit='pc',
        )
        self.product = self.make_product(self.organization, self.category, self.unit, 'Router', 'RTR-WS-1')
        self.serialized_product = self.make_product(
            self.organization, self.category, self.unit, 'Managed Router', 'RTR-WS-SERIAL', serialized=True
        )
        self.supplier = Supplier.objects.create(
            organization=self.organization,
            tenant=self.organization,
            company_name='Workspace Supply Ltd',
            created_by=self.admin,
        )
        other_unit = UnitOfMeasure.objects.create(
            organization=self.other_organization, tenant=self.other_organization, name='Unit', symbol='pc'
        )
        other_category = ProductCategory.objects.create(
            organization=self.other_organization,
            tenant=self.other_organization,
            name='Private Equipment',
            default_unit=other_unit,
            measure_unit='pc',
        )
        self.other_product = self.make_product(
            self.other_organization, other_category, other_unit, 'Private Router', 'PRIVATE-RTR'
        )
        self.other_supplier = Supplier.objects.create(
            organization=self.other_organization,
            tenant=self.other_organization,
            company_name='Private Supplier',
            created_by=self.admin,
        )
        self.client.login(username='workspace-admin', password='pass')

    def make_product(self, organization, category, unit, name, sku, *, serialized=False):
        return Product.objects.create(
            organization=organization,
            tenant=organization,
            name=name,
            sku=sku,
            item_type=Product.ItemType.PHYSICAL,
            catalog_category=category,
            sales_unit=unit,
            measure_unit='pc',
            quantity=Decimal('0.000000'),
            stock=0,
            track_stock=True,
            is_serialized=serialized,
            buying_price=Decimal('100.000000'),
            selling_price=Decimal('150.00'),
        )

    def purchase(self, reference='PUR-WS-1', *, organization=None, supplier=None, product=None, serialized=''):
        organization = organization or self.organization
        purchase = Purchase.objects.create(
            organization=organization,
            tenant=organization,
            supplier=supplier or self.supplier,
            reference_number=reference,
            purchase_date=date.today(),
            created_by=self.admin,
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            product=product or self.product,
            quantity=Decimal('2.000000'),
            unit_cost=Decimal('100.000000'),
            serial_numbers=serialized,
        )
        return purchase

    def payload(self, *, action='save_review', supplier=None, product=None, reference='PUR-WS-NEW', **line):
        payload = {
            'action': action,
            'supplier': supplier or self.supplier.pk,
            'reference_number': reference,
            'auto_generated_reference': '',
            'purchase_date': date.today().isoformat(),
            'notes': 'Workspace delivery',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': product or self.product.pk,
            'lines-0-quantity': '2',
            'lines-0-unit_cost': '100',
            'lines-0-batch_reference': '',
            'lines-0-expiry_date': '',
            'lines-0-serial_numbers': '',
        }
        payload.update({f'lines-0-{key}': value for key, value in line.items()})
        return payload

    def edit_payload(self, purchase, *, action='save_review', **line):
        payload = self.payload(action=action, reference=purchase.reference_number, **line)
        payload['lines-INITIAL_FORMS'] = '1'
        payload['lines-0-id'] = str(purchase.lines.get().pk)
        return payload

    def test_save_draft_continue_redirects_to_workspace_without_stock_change(self):
        response = self.client.post(
            reverse('inventory:purchase_create'), self.payload(action='save_continue')
        )
        purchase = Purchase.objects.get(reference_number='PUR-WS-NEW')
        self.assertRedirects(response, reverse('inventory:purchase_edit', args=[purchase.pk]))
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertEqual(purchase.lines.count(), 1)
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(InventoryBalance.objects.exists())

    def test_save_review_redirects_to_detail_without_stock_change(self):
        response = self.client.post(
            reverse('inventory:purchase_create'), self.payload(action='save_review')
        )
        purchase = Purchase.objects.get(reference_number='PUR-WS-NEW')
        self.assertRedirects(response, reverse('inventory:purchase_detail', args=[purchase.pk]))
        self.assertEqual(purchase.total_cost, Decimal('200.00'))
        self.assertFalse(StockMovement.objects.exists())

    def test_invalid_submission_preserves_values_and_changes_no_stock(self):
        response = self.client.post(
            reverse('inventory:purchase_create'),
            self.payload(reference='KEEP-ME', quantity=''),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'KEEP-ME')
        self.assertContains(response, 'Enter the quantity received in the product sales unit.')
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_unknown_and_missing_actions_fail_safely(self):
        for action in ('unexpected', None):
            with self.subTest(action=action):
                payload = self.payload(reference=f'INVALID-{action}')
                if action is None:
                    payload.pop('action')
                else:
                    payload['action'] = action
                response = self.client.post(reverse('inventory:purchase_create'), payload)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Choose a valid purchase action')
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_confirm_receive_saves_then_requires_confirmation_review(self):
        response = self.client.post(
            reverse('inventory:purchase_create'), self.payload(action='confirm_receive')
        )
        purchase = Purchase.objects.get(reference_number='PUR-WS-NEW')
        self.assertRedirects(
            response, f"{reverse('inventory:purchase_detail', args=[purchase.pk])}?receive=1"
        )
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertFalse(StockMovement.objects.exists())
        review = self.client.get(response.url)
        self.assertContains(review, 'Final confirmation required')
        self.assertContains(review, 'Confirm &amp; Receive Stock', html=False)

    def test_confirm_requires_permission_and_crafted_post_cannot_receive(self):
        purchase = self.purchase()
        self.client.logout()
        self.client.login(username='workspace-sales', password='pass')
        response = self.client.post(reverse('inventory:purchase_confirm', args=[purchase.pk]))
        self.assertEqual(response.status_code, 403)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertFalse(StockMovement.objects.exists())

    def test_cross_tenant_supplier_product_and_purchase_injection_is_rejected(self):
        supplier_response = self.client.post(
            reverse('inventory:purchase_create'),
            self.payload(supplier=self.other_supplier.pk, reference='CROSS-SUPPLIER'),
        )
        product_response = self.client.post(
            reverse('inventory:purchase_create'),
            self.payload(product=self.other_product.pk, reference='CROSS-PRODUCT'),
        )
        other_purchase = self.purchase(
            'OTHER-PURCHASE',
            organization=self.other_organization,
            supplier=self.other_supplier,
            product=self.other_product,
        )
        edit_response = self.client.post(
            reverse('inventory:purchase_edit', args=[other_purchase.pk]), self.payload()
        )
        self.assertEqual(supplier_response.status_code, 200)
        self.assertEqual(product_response.status_code, 200)
        self.assertContains(supplier_response, 'Select a valid choice')
        self.assertContains(product_response, 'Select a valid choice')
        self.assertEqual(edit_response.status_code, 404)
        self.assertFalse(Purchase.objects.filter(tenant=self.organization).exists())

    def test_final_confirmation_calls_existing_service_boundary(self):
        purchase = self.purchase()
        with patch.object(
            InventoryService, 'confirm_purchase', wraps=InventoryService.confirm_purchase
        ) as confirm_purchase:
            response = self.client.post(reverse('inventory:purchase_confirm', args=[purchase.pk]))
        self.assertRedirects(response, reverse('inventory:purchase_detail', args=[purchase.pk]))
        confirm_purchase.assert_called_once_with(
            organization=self.organization, purchase_id=purchase.pk, actor=self.admin
        )

    def test_successful_confirmation_posts_stock_once_and_retry_is_idempotent(self):
        purchase = self.purchase()
        confirm_url = reverse('inventory:purchase_confirm', args=[purchase.pk])
        self.client.post(confirm_url)
        self.client.post(confirm_url)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.CONFIRMED)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2.000000'))
        self.assertEqual(StockMovement.objects.filter(purchase_line__purchase=purchase).count(), 1)

    def test_received_purchase_cannot_be_edited(self):
        purchase = self.purchase()
        InventoryService.confirm_purchase(
            organization=self.organization, purchase_id=purchase.pk, actor=self.admin
        )
        response = self.client.post(
            reverse('inventory:purchase_edit', args=[purchase.pk]), self.edit_payload(purchase)
        )
        self.assertRedirects(response, reverse('inventory:purchase_detail', args=[purchase.pk]))
        self.assertContains(self.client.get(response.url), 'Stock received.')

    def test_confirm_action_visibility_follows_purchase_permission(self):
        admin_workspace = self.client.get(reverse('inventory:purchase_create'))
        self.assertContains(admin_workspace, 'value="confirm_receive"')
        self.client.logout()
        self.client.login(username='workspace-sales', password='pass')
        unauthorized_workspace = self.client.get(reverse('inventory:purchase_create'))
        self.assertEqual(unauthorized_workspace.status_code, 403)
        self.assertNotContains(unauthorized_workspace, 'value="confirm_receive"', status_code=403)

    def test_quantity_and_cost_are_saved_in_the_selected_sales_unit(self):
        response = self.client.post(
            reverse('inventory:purchase_create'),
            self.payload(
                action='save_review',
                quantity='3.5',
                unit_cost='750',
            ),
        )
        purchase = Purchase.objects.get(reference_number='PUR-WS-NEW')
        self.assertRedirects(response, reverse('inventory:purchase_detail', args=[purchase.pk]))
        line = purchase.lines.get()
        self.assertEqual(line.quantity, Decimal('3.500000'))
        self.assertEqual(line.unit_cost, Decimal('750.000000'))
        self.assertEqual(purchase.total_cost, Decimal('2625.00'))

    def test_existing_serial_validation_blocks_invalid_draft_and_stock(self):
        response = self.client.post(
            reverse('inventory:purchase_create'),
            self.payload(
                product=self.serialized_product.pk,
                quantity='2',
                serial_numbers='ONLY-ONE',
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter exactly 2 unique serial numbers.')
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(StockUnit.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
