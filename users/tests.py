from django.test import TestCase
from django.apps import apps
from django.contrib.auth import get_user_model
from importlib import import_module
from django.urls import reverse

from customers.models import Customer
from users.models import Organization


User = get_user_model()


class TenantBackfillMigrationTests(TestCase):
    def test_existing_records_are_assigned_default_tenant(self):
        migration = import_module("users.migrations.0006_backfill_tenant_and_access_profiles")
        user = User.objects.create_user(username="legacy-user", password="pass")
        customer = Customer.all_objects.create(
            organization=None,
            tenant=None,
            name="Legacy Customer",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )

        migration.forwards(apps, None)

        customer.refresh_from_db()
        self.assertIsNotNone(customer.organization_id)
        self.assertIsNotNone(customer.tenant_id)
        self.assertEqual(customer.organization_id, customer.tenant_id)
        self.assertTrue(Organization.objects.filter(slug="default-tenant").exists())
        self.assertTrue(hasattr(user, "access_profile"))


class AdminSiteAccessTests(TestCase):
    def test_tenant_admin_cannot_access_django_admin(self):
        org = Organization.objects.create(name="Tenant A", slug="tenant-a")
        user = User.objects.create_user(username="tenant-admin", password="pass")
        user.is_staff = True
        user.save(update_fields=["is_staff"])

        from users.models import UserAccessProfile

        UserAccessProfile.objects.create(user=user, tenant=org, role=UserAccessProfile.Role.TENANT_ADMIN)

        self.client.login(username="tenant-admin", password="pass")
        response = self.client.get(reverse("admin:index"))
        self.assertIn(response.status_code, {302, 403})
