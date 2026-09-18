from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile


User = get_user_model()


class PurchaseProductQuickCreateTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name='Quick Product Tenant', slug='quick-product-tenant')
        self.other = Organization.objects.create(name='Other Product Tenant', slug='other-product-tenant')
        self.admin = User.objects.create_user(username='quick-product-admin', password='pass')
        UserAccessProfile.objects.create(user=self.admin, tenant=self.tenant, role=UserAccessProfile.Role.TENANT_ADMIN)
        self.unit = UnitOfMeasure.objects.create(organization=self.tenant, tenant=self.tenant, name='Piece', symbol='pc')
        self.category = ProductCategory.objects.create(
            organization=self.tenant, tenant=self.tenant, name='Routers', default_unit=self.unit,
        )
        self.foreign_unit = UnitOfMeasure.objects.create(organization=self.other, tenant=self.other, name='Other', symbol='x')
        self.foreign_category = ProductCategory.objects.create(
            organization=self.other, tenant=self.other, name='Foreign', default_unit=self.foreign_unit,
        )
        self.url = reverse('inventory:purchase_product_quick_create')

    def payload(self, **updates):
        data = {'name': 'MikroTik Router', 'catalog_category': self.category.pk, 'sales_unit': self.unit.pk, 'selling_price': '150000'}
        data.update(updates)
        return data

    def test_login_permission_and_post_are_required(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.client.login(username='quick-product-admin', password='pass')
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_required_and_tenant_scoped_choices_are_enforced(self):
        self.client.login(username='quick-product-admin', password='pass')
        response = self.client.post(self.url, self.payload(name='', selling_price=''))
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.json()['errors'])
        self.assertIn('selling_price', response.json()['errors'])
        response = self.client.post(self.url, self.payload(catalog_category=self.foreign_category.pk, sales_unit=self.foreign_unit.pk))
        self.assertEqual(response.status_code, 400)

    def test_unit_must_be_allowed_by_category(self):
        other_unit = UnitOfMeasure.objects.create(organization=self.tenant, tenant=self.tenant, name='Meter', symbol='m')
        self.client.login(username='quick-product-admin', password='pass')
        response = self.client.post(self.url, self.payload(sales_unit=other_unit.pk))
        self.assertEqual(response.status_code, 400)
        self.assertIn('allowed', response.json()['errors']['sales_unit'][0]['message'])

    def test_creates_stock_product_with_generated_sku_and_audit(self):
        self.client.login(username='quick-product-admin', password='pass')
        response = self.client.post(self.url, self.payload(is_serialized='on', track_expiry='on', buying_price='999999'))
        self.assertEqual(response.status_code, 201)
        product = Product.objects.get(tenant=self.tenant)
        self.assertTrue(product.sku)
        self.assertEqual(product.buying_price, 0)
        self.assertEqual(product.quantity, 0)
        self.assertTrue(product.track_stock)
        self.assertTrue(product.is_serialized)
        self.assertTrue(product.track_expiry)
        self.assertTrue(AuditLog.objects.filter(tenant=self.tenant, action='inventory.product.created_quick').exists())

    def test_duplicate_active_name_is_rejected_and_workspace_exposes_modal(self):
        self.client.login(username='quick-product-admin', password='pass')
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 201)
        response = self.client.post(self.url, self.payload(name='mikrotik router'))
        self.assertEqual(response.status_code, 400)
        workspace = self.client.get(reverse('inventory:purchase_create'))
        self.assertContains(workspace, 'data-open-quick-product')
        self.assertContains(workspace, 'data-product-quick-create-url')
        self.assertContains(workspace, 'data-category-quick-create-url')
