from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from audit.models import AuditLog
from users.models import Organization, UserAccessProfile

from .models import Supplier


User = get_user_model()


class PurchaseSupplierQuickCreateTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Quick Supplier Tenant', slug='quick-supplier-tenant')
        self.other_organization = Organization.objects.create(name='Other Supplier Tenant', slug='other-quick-supplier-tenant')
        self.admin = User.objects.create_user(username='quick-supplier-admin', password='pass')
        UserAccessProfile.objects.create(
            user=self.admin,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.sales = User.objects.create_user(username='quick-supplier-sales', password='pass')
        UserAccessProfile.objects.create(
            user=self.sales,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_STAFF,
        )
        self.url = reverse('inventory:purchase_supplier_quick_create')

    def test_authentication_permission_and_post_are_required(self):
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.client.login(username='quick-supplier-sales', password='pass')
        self.assertEqual(self.client.post(self.url, {'company_name': 'Blocked', 'phone': '+255700000000'}).status_code, 403)
        self.client.logout()
        self.client.login(username='quick-supplier-admin', password='pass')
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.login(username='quick-supplier-admin', password='pass')
        self.assertEqual(client.post(self.url, {'company_name': 'No CSRF', 'phone': '+255700000001'}).status_code, 403)

    def test_name_and_phone_are_required_with_structured_errors(self):
        self.client.login(username='quick-supplier-admin', password='pass')
        response = self.client.post(self.url, {'company_name': '', 'phone': ''})
        self.assertEqual(response.status_code, 400)
        self.assertIn('company_name', response.json()['errors'])
        self.assertIn('phone', response.json()['errors'])
        self.assertFalse(Supplier.objects.filter(tenant=self.organization).exists())

    def test_supplier_is_created_in_active_tenant_and_audited(self):
        self.client.login(username='quick-supplier-admin', password='pass')
        response = self.client.post(self.url, {
            'company_name': '  Moshi Network Supply  ',
            'phone': ' +255712345678 ',
            'physical_address': 'Must not be accepted by the minimal form',
        })

        self.assertEqual(response.status_code, 201)
        supplier = Supplier.objects.get(tenant=self.organization)
        self.assertEqual(supplier.company_name, 'Moshi Network Supply')
        self.assertEqual(supplier.phone, '+255712345678')
        self.assertEqual(supplier.physical_address, '')
        self.assertEqual(supplier.created_by, self.admin)
        self.assertTrue(supplier.is_active)
        self.assertEqual(response.json()['supplier']['id'], supplier.pk)
        self.assertTrue(AuditLog.objects.filter(
            tenant=self.organization,
            action='inventory.supplier.created_quick',
            object_id=str(supplier.pk),
        ).exists())

    def test_duplicate_is_rejected_case_insensitively_within_tenant(self):
        Supplier.objects.create(
            organization=self.organization,
            tenant=self.organization,
            company_name='Existing Supplier',
            phone='+255700000001',
            created_by=self.admin,
        )
        self.client.login(username='quick-supplier-admin', password='pass')
        response = self.client.post(self.url, {
            'company_name': 'existing supplier',
            'phone': '+255700000002',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('already exists', response.json()['errors']['company_name'][0]['message'])
        self.assertEqual(Supplier.objects.filter(tenant=self.organization).count(), 1)

    def test_same_name_in_another_tenant_does_not_block_creation(self):
        Supplier.objects.create(
            organization=self.other_organization,
            tenant=self.other_organization,
            company_name='Shared Supplier Name',
            phone='+255700000003',
            created_by=self.admin,
        )
        self.client.login(username='quick-supplier-admin', password='pass')
        response = self.client.post(self.url, {
            'company_name': 'Shared Supplier Name',
            'phone': '+255700000004',
        })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Supplier.objects.filter(
            tenant=self.organization,
            company_name='Shared Supplier Name',
        ).exists())

    def test_purchase_workspace_exposes_quick_create_without_prefilling_data(self):
        self.client.login(username='quick-supplier-admin', password='pass')
        response = self.client.get(reverse('inventory:purchase_create'))
        self.assertContains(response, 'data-supplier-quick-create-url=')
        self.assertContains(response, 'data-open-quick-supplier')
        self.assertContains(response, 'data-quick-supplier-dialog')

