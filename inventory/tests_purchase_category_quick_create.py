from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from audit.models import AuditLog
from products.models import ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile


User = get_user_model()


class PurchaseCategoryQuickCreateTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Quick Category Tenant', slug='quick-category-tenant')
        self.other_organization = Organization.objects.create(name='Other Category Tenant', slug='other-category-tenant')
        self.admin = User.objects.create_user(username='quick-category-admin', password='pass')
        UserAccessProfile.objects.create(
            user=self.admin,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.staff = User.objects.create_user(username='quick-category-staff', password='pass')
        UserAccessProfile.objects.create(
            user=self.staff,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_STAFF,
        )
        self.unit = UnitOfMeasure.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Piece',
            symbol='pc',
        )
        self.inactive_unit = UnitOfMeasure.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Inactive unit',
            symbol='old',
            is_active=False,
        )
        self.foreign_unit = UnitOfMeasure.objects.create(
            organization=self.other_organization,
            tenant=self.other_organization,
            name='Foreign unit',
            symbol='x',
        )
        self.url = reverse('inventory:purchase_category_quick_create')

    def test_authentication_permission_and_post_are_required(self):
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.client.login(username='quick-category-staff', password='pass')
        self.assertEqual(self.client.post(self.url, {'name': 'Blocked', 'default_unit': self.unit.pk}).status_code, 403)
        self.client.logout()
        self.client.login(username='quick-category-admin', password='pass')
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.login(username='quick-category-admin', password='pass')
        self.assertEqual(client.post(self.url, {'name': 'No CSRF', 'default_unit': self.unit.pk}).status_code, 403)

    def test_name_and_default_unit_are_required_with_structured_errors(self):
        self.client.login(username='quick-category-admin', password='pass')
        response = self.client.post(self.url, {'name': '', 'default_unit': ''})
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.json()['errors'])
        self.assertIn('default_unit', response.json()['errors'])
        self.assertFalse(ProductCategory.objects.filter(tenant=self.organization).exists())

    def test_category_is_created_with_one_allowed_default_unit_and_audit(self):
        self.client.login(username='quick-category-admin', password='pass')
        response = self.client.post(self.url, {
            'name': '  Network Equipment  ',
            'default_unit': self.unit.pk,
            'description': 'Must be ignored by the minimal form',
            'icon': ProductCategory.Icon.CAMERA,
        })

        self.assertEqual(response.status_code, 201)
        category = ProductCategory.objects.get(tenant=self.organization)
        self.assertEqual(category.name, 'Network Equipment')
        self.assertEqual(category.organization, self.organization)
        self.assertEqual(category.tenant, self.organization)
        self.assertEqual(category.description, '')
        self.assertEqual(category.icon, ProductCategory.Icon.GENERIC)
        self.assertEqual(category.default_unit, self.unit)
        self.assertEqual(list(category.allowed_units.all()), [self.unit])
        self.assertEqual(response.json()['category']['default_unit']['id'], self.unit.pk)
        self.assertTrue(AuditLog.objects.filter(
            tenant=self.organization,
            action='inventory.category.created_quick',
            object_id=str(category.pk),
        ).exists())

    def test_foreign_and_inactive_units_are_rejected(self):
        self.client.login(username='quick-category-admin', password='pass')
        for unit in (self.foreign_unit, self.inactive_unit):
            response = self.client.post(self.url, {'name': f'Category {unit.pk}', 'default_unit': unit.pk})
            self.assertEqual(response.status_code, 400)
            self.assertIn('default_unit', response.json()['errors'])
        self.assertFalse(ProductCategory.objects.filter(tenant=self.organization).exists())

    def test_duplicate_is_rejected_case_insensitively_within_tenant(self):
        ProductCategory.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Existing Category',
            default_unit=self.unit,
        )
        self.client.login(username='quick-category-admin', password='pass')
        response = self.client.post(self.url, {'name': 'existing category', 'default_unit': self.unit.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn('already exists', response.json()['errors']['name'][0]['message'])
        self.assertEqual(ProductCategory.objects.filter(tenant=self.organization).count(), 1)

    def test_same_name_in_another_tenant_does_not_block_creation(self):
        ProductCategory.objects.create(
            organization=self.other_organization,
            tenant=self.other_organization,
            name='Shared Category',
            default_unit=self.foreign_unit,
        )
        self.client.login(username='quick-category-admin', password='pass')
        response = self.client.post(self.url, {'name': 'Shared Category', 'default_unit': self.unit.pk})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(ProductCategory.objects.filter(
            tenant=self.organization,
            name='Shared Category',
        ).exists())
