from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from customers.models import Customer

from .models import Organization, SupportAccessSession, TenantMembership


class TenantAdminScopingTests(TestCase):
    def setUp(self):
        self.tenant_a = Organization.objects.create(name="Admin Tenant A", slug="admin-tenant-a")
        self.tenant_b = Organization.objects.create(name="Admin Tenant B", slug="admin-tenant-b")
        self.user = get_user_model().objects.create_superuser(
            username="platform-admin", email="platform@example.com", password="test-pass-123",
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a, user=self.user,
            base_role=TenantMembership.BaseRole.SUPER_ADMIN,
        )
        self.customer_a = Customer.objects.create(
            tenant=self.tenant_a, organization=self.tenant_a, name="Visible Support Customer",
            customer_type="internet", location="Arusha",
        )
        self.customer_b = Customer.objects.create(
            tenant=self.tenant_b, organization=self.tenant_b, name="Hidden Cross Tenant Customer",
            customer_type="internet", location="Mwanza",
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _start_support_context(self):
        session = self.client.session
        session.save()
        support = SupportAccessSession.objects.create(
            actor=self.user, tenant=self.tenant_a, reason="Investigate tenant support ticket",
            session_key=session.session_key,
        )
        session["support_access_session_id"] = support.pk
        session.save()

    def test_tenant_admin_model_is_unavailable_without_support_context(self):
        response = self.client.get(reverse("admin:customers_customer_changelist"))
        self.assertEqual(response.status_code, 403)

    def test_support_context_scopes_admin_list_and_object_lookup(self):
        self._start_support_context()
        response = self.client.get(reverse("admin:customers_customer_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.customer_a.name)
        self.assertNotContains(response, self.customer_b.name)
        response = self.client.get(reverse("admin:customers_customer_change", args=[self.customer_b.pk]))
        self.assertEqual(response.status_code, 302)  # Django admin safely redirects when object is outside queryset.
