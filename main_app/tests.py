from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from customers.models import Customer
from users.models import Organization, TenantMembership


User = get_user_model()


class WorkspaceLandingTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name="Workspace Tenant", slug="workspace-tenant")
        self.manager = self._member("workspace-manager", TenantMembership.BaseRole.ADMIN_MANAGER)
        self.sales = self._member("workspace-sales", TenantMembership.BaseRole.SALES)
        self.technician = self._member("workspace-tech", TenantMembership.BaseRole.TECHNICIAN)
        self.super_admin = self._member("workspace-super", TenantMembership.BaseRole.SUPER_ADMIN)
        Customer.objects.create(
            tenant=self.tenant, organization=self.tenant, name="Workspace Customer",
            customer_type="internet", location="Arusha",
        )

    def _member(self, username, role):
        user = User.objects.create_user(username=username, password="test-pass-123")
        return TenantMembership.objects.create(tenant=self.tenant, user=user, base_role=role)

    def _login(self, membership, *, next_url=None):
        client = Client()
        payload = {"username": membership.user.username, "password": "test-pass-123"}
        if next_url is not None:
            payload["next"] = next_url
        return client, client.post(reverse("login"), payload)

    def test_manager_login_enters_operational_workspace_not_customer_table(self):
        client, response = self._login(self.manager)
        self.assertRedirects(response, reverse("main_app:workspace_home"), fetch_redirect_response=False)
        response = client.get(reverse("main_app:workspace_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operations overview")
        self.assertContains(response, "Active customers")
        self.assertNotContains(response, "Revenue")

    def test_sales_login_enters_own_sales_workspace(self):
        client, response = self._login(self.sales)
        self.assertRedirects(response, reverse("main_app:workspace_home"), fetch_redirect_response=False)
        response = client.get(reverse("main_app:workspace_home"))
        self.assertContains(response, "My sales workspace")
        self.assertNotContains(response, "Work approvals")
        self.assertNotContains(response, "Active customers")
        self.assertNotContains(response, "Active services")
        self.assertNotContains(response, "Revenue")

    def test_technician_login_enters_only_work_reports(self):
        client, response = self._login(self.technician)
        self.assertRedirects(response, reverse("main_app:workspace_home"), fetch_redirect_response=False)
        response = client.get(reverse("main_app:workspace_home"), follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse("work_reports:list"))
        self.assertContains(response, "My Work Reports")

    def test_super_administrator_must_choose_audited_support_context(self):
        client, response = self._login(self.super_admin)
        self.assertRedirects(response, reverse("main_app:workspace_home"), fetch_redirect_response=False)
        response = client.get(reverse("main_app:workspace_home"))
        self.assertRedirects(response, reverse("start_support_access"), fetch_redirect_response=False)

    def test_safe_next_url_is_preserved_for_a_deep_link(self):
        client = Client()
        destination = reverse("customer-list")
        login_page = client.get(destination)
        self.assertEqual(login_page.status_code, 302)
        self.assertIn("next=/customers/", login_page["Location"])
        login_form = client.get(login_page["Location"])
        self.assertContains(login_form, 'name="next"')
        response = client.post(reverse("login"), {
            "username": self.sales.user.username, "password": "test-pass-123", "next": destination,
        })
        self.assertRedirects(response, destination, fetch_redirect_response=False)

    def test_unsafe_external_next_url_falls_back_to_workspace(self):
        _client, response = self._login(self.sales, next_url="https://untrusted.example/steal-session")
        self.assertRedirects(response, reverse("main_app:workspace_home"), fetch_redirect_response=False)
