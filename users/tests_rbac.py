from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from billing.models import BillingDocument
from customers.models import Customer
from audit.models import AuditLog
from users.models import Organization, TenantMembership
from users.permissions import (
    PermissionCode, has_tenant_permission, sales_document_queryset_for,
    validate_delegated_grant,
)


User = get_user_model()


class TenantRBACSecurityTests(TestCase):
    def setUp(self):
        self.tenant_a = Organization.objects.create(name="Tenant A", slug="rbac-a")
        self.tenant_b = Organization.objects.create(name="Tenant B", slug="rbac-b")
        self.manager = User.objects.create_user("manager-rbac", password="pass")
        self.sales_a = User.objects.create_user("sales-a", password="pass")
        self.sales_b = User.objects.create_user("sales-b", password="pass")
        self.tech = User.objects.create_user("tech-rbac", password="pass")
        self.manager_membership = TenantMembership.objects.create(
            tenant=self.tenant_a, user=self.manager, base_role=TenantMembership.BaseRole.ADMIN_MANAGER
        )
        self.sales_a_membership = TenantMembership.objects.create(
            tenant=self.tenant_a, user=self.sales_a, base_role=TenantMembership.BaseRole.SALES
        )
        self.sales_b_membership = TenantMembership.objects.create(
            tenant=self.tenant_a, user=self.sales_b, base_role=TenantMembership.BaseRole.SALES
        )
        self.tech_membership = TenantMembership.objects.create(
            tenant=self.tenant_a, user=self.tech, base_role=TenantMembership.BaseRole.TECHNICIAN
        )
        self.customer = Customer.objects.create(
            tenant=self.tenant_a, organization=self.tenant_a, name="Customer",
            customer_type="random", status=Customer.Status.ACTIVE, location="Moshi",
        )
        self.other_customer = Customer.objects.create(
            tenant=self.tenant_b, organization=self.tenant_b, name="Other Customer",
            customer_type="random", status=Customer.Status.ACTIVE, location="Arusha",
        )
        self.own_invoice = self._invoice(self.tenant_a, self.customer, "INV-OWN", self.sales_a_membership)
        self.other_sales_invoice = self._invoice(self.tenant_a, self.customer, "INV-OTHER-SALES", self.sales_b_membership)
        self.other_tenant_invoice = self._invoice(self.tenant_b, self.other_customer, "INV-OTHER-TENANT", None)

    def _invoice(self, tenant, customer, number, membership):
        return BillingDocument.objects.unscoped().create(
            tenant=tenant, organization=tenant, customer=customer,
            document_type=BillingDocument.DocumentType.INVOICE, number=number,
            issue_date=date.today(), status=BillingDocument.Status.DRAFT,
            total=Decimal("100.00"), created_by=membership.user if membership else self.manager,
            created_by_membership=membership, responsible_membership=membership,
        )

    def test_sales_queryset_is_own_or_assigned_from_the_start(self):
        ids = set(sales_document_queryset_for(self.sales_a, self.tenant_a).values_list("id", flat=True))
        self.assertEqual(ids, {self.own_invoice.id})

    def test_sales_url_tampering_returns_not_found(self):
        self.client.force_login(self.sales_a)
        response = self.client.get(reverse("billing:document_detail", kwargs={"doc_type": "invoice", "pk": self.other_sales_invoice.pk}))
        self.assertEqual(response.status_code, 404)
        response = self.client.get(reverse("billing:document_pdf", kwargs={"doc_type": "invoice", "pk": self.other_tenant_invoice.pk}))
        self.assertEqual(response.status_code, 404)

    def test_sales_can_complete_own_invoice_in_installments_but_not_another_sales_invoice(self):
        self.own_invoice.status = BillingDocument.Status.ISSUED
        self.own_invoice.save(update_fields=["status"])
        self.other_sales_invoice.status = BillingDocument.Status.ISSUED
        self.other_sales_invoice.save(update_fields=["status"])
        self.client.force_login(self.sales_a)

        first = self.client.post(
            reverse("billing:create_receipt_from_invoice", args=[self.own_invoice.pk]),
            {
                "amount_paid": "40.00",
                "payment_date": date.today().isoformat(),
                "payment_method": "cash",
                "payment_reference": "sales-own-partial",
                "notes": "First installment",
            },
        )
        self.assertEqual(first.status_code, 302)
        self.own_invoice.refresh_from_db()
        self.assertEqual(self.own_invoice.status, BillingDocument.Status.PARTIALLY_PAID)

        denied = self.client.post(
            reverse("billing:create_receipt_from_invoice", args=[self.other_sales_invoice.pk]),
            {
                "amount_paid": "100.00",
                "payment_date": date.today().isoformat(),
                "payment_method": "cash",
            },
        )
        self.assertEqual(denied.status_code, 404)

        final = self.client.post(
            reverse("billing:create_receipt_from_invoice", args=[self.own_invoice.pk]),
            {
                "amount_paid": "60.00",
                "payment_date": date.today().isoformat(),
                "payment_method": "cash",
                "payment_reference": "sales-own-final",
                "notes": "Final installment",
            },
        )
        self.assertEqual(final.status_code, 302)
        self.own_invoice.refresh_from_db()
        self.assertEqual(self.own_invoice.status, BillingDocument.Status.PAID)
        self.assertEqual(self.own_invoice.receipts.count(), 2)

    def test_sales_cannot_receive_tenant_wide_finance(self):
        self.assertFalse(has_tenant_permission(self.sales_a, self.tenant_a, PermissionCode.FINANCE_SALES_VIEW_ALL))
        with self.assertRaises(PermissionDenied):
            validate_delegated_grant(
                actor_membership=self.manager_membership,
                target_membership=self.sales_a_membership,
                action_code=PermissionCode.FINANCE_PROFITABILITY_VIEW,
                scope="TENANT_ALL",
            )

    def test_technician_is_deny_by_default(self):
        self.assertFalse(has_tenant_permission(self.tech, self.tenant_a, PermissionCode.CUSTOMERS_VIEW))
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(reverse("customer-list")).status_code, 403)

    def test_manager_sees_only_own_tenant_documents(self):
        ids = set(sales_document_queryset_for(self.manager, self.tenant_a).values_list("id", flat=True))
        self.assertEqual(ids, {self.own_invoice.id, self.other_sales_invoice.id})

    def test_manager_cannot_edit_self_or_cross_tenant(self):
        other_manager = TenantMembership.objects.create(
            tenant=self.tenant_b, user=self.sales_b, base_role=TenantMembership.BaseRole.ADMIN_MANAGER
        )
        with self.assertRaises(PermissionDenied):
            validate_delegated_grant(
                actor_membership=self.manager_membership, target_membership=self.manager_membership,
                action_code=PermissionCode.CUSTOMERS_VIEW, scope="OWN",
            )
        with self.assertRaises(PermissionDenied):
            validate_delegated_grant(
                actor_membership=self.manager_membership, target_membership=other_manager,
                action_code=PermissionCode.CUSTOMERS_VIEW, scope="OWN",
            )

    def test_team_access_is_a_tenant_scoped_directory_with_progressive_management(self):
        self.sales_a.first_name = "Asha"
        self.sales_a.last_name = "Sales"
        self.sales_a.email = "asha@example.test"
        self.sales_a.save()
        self.tech.set_unusable_password()
        self.tech.save(update_fields=["password"])
        AuditLog.objects.create(
            organization=self.tenant_a,
            tenant=self.tenant_a,
            actor=self.manager,
            action="security.member.access_changed",
            object_type="TenantMembership",
            object_id=str(self.sales_a_membership.pk),
            old_value={"base_role": TenantMembership.BaseRole.TECHNICIAN},
            new_value={"base_role": TenantMembership.BaseRole.SALES},
        )

        self.client.force_login(self.manager)
        response = self.client.get(reverse("team_access"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Team &amp; Access")
        self.assertContains(response, "Manage workspace members, roles, and access.")
        self.assertContains(response, "Asha Sales")
        self.assertContains(response, "Sales workflow only")
        self.assertContains(response, "Invited")
        self.assertContains(response, "Manager-only financial controls")
        self.assertContains(response, "changed role from technician to sales")
        self.assertNotContains(response, ">customers.create<")
        self.assertContains(response, f'data-team-drawer="{self.sales_a_membership.pk}"', html=False)
        self.assertNotContains(response, f'data-team-drawer="{self.manager_membership.pk}"', html=False)

    def test_sales_cannot_open_team_access(self):
        self.client.force_login(self.sales_a)
        self.assertEqual(self.client.get(reverse("team_access")).status_code, 403)

    def test_invited_member_receives_one_time_password_setup_link_and_can_log_in(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("invite_member"),
            {"email": "new.sales@example.test", "first_name": "Neema", "last_name": "Mushi", "base_role": "SALES"},
        )

        self.assertRedirects(response, reverse("team_access"))
        invited = User.objects.get(email="new.sales@example.test")
        self.assertFalse(invited.has_usable_password())
        membership = TenantMembership.objects.get(tenant=self.tenant_a, user=invited)
        self.assertTrue(membership.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Set up your Tenant A workspace account", mail.outbox[0].subject)
        self.assertIn("/users/reset/", mail.outbox[0].body)
        self.assertIn(invited.username, mail.outbox[0].body)

        resend = self.client.post(reverse("resend_member_activation", args=[membership.pk]))
        self.assertRedirects(resend, reverse("team_access"))
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(AuditLog.objects.filter(
            tenant=self.tenant_a,
            action="security.member.activation_resent",
            object_id=str(membership.pk),
        ).exists())

        activation_path = mail.outbox[-1].body.split("http://testserver", 1)[1].splitlines()[0]
        activation_page = self.client.get(activation_path, follow=True)
        self.assertEqual(activation_page.status_code, 200)
        self.assertContains(activation_page, "Set new password")
        completion = self.client.post(
            activation_page.request["PATH_INFO"],
            {"new_password1": "S@feActivationPass123", "new_password2": "S@feActivationPass123"},
            follow=True,
        )
        self.assertEqual(completion.status_code, 200)
        invited.refresh_from_db()
        self.assertTrue(invited.has_usable_password())
        self.assertTrue(self.client.login(username=invited.username, password="S@feActivationPass123"))
