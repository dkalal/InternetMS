from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from .models import Supplier


class PurchaseFullCreateModalTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Modal Tenant', slug='modal-tenant')
        self.user = get_user_model().objects.create_user(username='modal-admin', password='pass')
        UserAccessProfile.objects.create(
            user=self.user, tenant=self.organization, role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.unit = UnitOfMeasure.objects.create(
            organization=self.organization, tenant=self.organization, name='Piece', symbol='pc',
        )
        self.category = ProductCategory.objects.create(
            organization=self.organization, tenant=self.organization, name='Routers', default_unit=self.unit,
        )
        self.category.allowed_units.add(self.unit)
        self.client.force_login(self.user)

    def test_workspace_links_to_full_create_forms(self):
        response = self.client.get(reverse('inventory:purchase_create'))
        self.assertContains(response, 'data-create-record-dialog')
        self.assertContains(response, 'data-create-record-kind="supplier"')
        self.assertContains(response, 'data-create-record-kind="product"')

    def test_supplier_modal_renders_and_saves_advanced_fields(self):
        url = reverse('inventory:supplier_create') + '?purchase_modal=1'
        response = self.client.get(url)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        for field in ('company_name', 'contact_person', 'phone', 'email', 'physical_address', 'tin_vrn', 'notes', 'is_active'):
            self.assertContains(response, f'name="{field}"')
        response = self.client.post(url, {
            'company_name': 'Complete Supplier', 'phone': '+255700000000',
            'contact_person': 'Amina', 'email': 'amina@example.com',
            'physical_address': 'Arusha', 'tin_vrn': 'TIN-123', 'notes': 'Deliver Fridays',
            'is_active': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(response, 'purchase-modal-created')
        supplier = Supplier.objects.get(tenant=self.organization)
        self.assertEqual(supplier.contact_person, 'Amina')
        self.assertEqual(supplier.physical_address, 'Arusha')
        self.assertTrue(supplier.is_active)

    def test_product_modal_preserves_full_form_and_purchase_eligibility(self):
        url = reverse('product-create') + '?purchase_modal=1'
        response = self.client.get(url)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        for field in ('name', 'sku', 'image', 'brand', 'model_number', 'buying_price', 'selling_price',
                      'technician_price', 'wholesale_price', 'reorder_threshold', 'description'):
            self.assertContains(response, f'name="{field}"')
        response = self.client.post(url, {
            'name': 'Complete Router', 'sku': 'ROUTER-001',
            'catalog_category': self.category.pk, 'sales_unit': self.unit.pk,
            'item_type': Product.ItemType.SERVICE, 'track_stock': '', 'is_active': '',
            'buying_price': '100.00', 'selling_price': '150.00',
            'technician_price': '140.00', 'brand': 'MikroTik', 'model_number': 'RB-1',
            'description': 'Purchase ready', 'reorder_threshold': '2',
            'wholesale_min_quantity': '1', 'category': 'hardware', 'quantity': '500',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(response, 'purchase-modal-created')
        product = Product.objects.get(tenant=self.organization)
        self.assertEqual(product.brand, 'MikroTik')
        self.assertEqual(product.model_number, 'RB-1')
        self.assertEqual(product.buying_price, 100)
        self.assertEqual(product.item_type, Product.ItemType.PHYSICAL)
        self.assertTrue(product.track_stock)
        self.assertTrue(product.is_active)
        self.assertEqual(product.quantity, 0)

    def test_modal_creation_requires_owning_permissions(self):
        staff = get_user_model().objects.create_user(username='modal-staff', password='pass')
        UserAccessProfile.objects.create(
            user=staff, tenant=self.organization, role=UserAccessProfile.Role.TENANT_STAFF,
        )
        self.client.force_login(staff)
        self.assertEqual(
            self.client.get(reverse('inventory:supplier_create') + '?purchase_modal=1').status_code, 403,
        )
        self.assertEqual(
            self.client.get(reverse('product-create') + '?purchase_modal=1').status_code, 403,
        )
