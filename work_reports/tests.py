from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from customers.models import Customer
from users.models import Organization, TenantMembership
from users.permissions import PermissionCode, has_tenant_permission

from .models import TechnicianWorkReport, WorkReportHistory
from .services import approve_report, correct_approved_report, reject_report, submit_report


User = get_user_model()


class WorkReportSecurityTests(TestCase):
    def setUp(self):
        self.tenant_a = Organization.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Organization.objects.create(name="Tenant B", slug="tenant-b")
        self.manager_a = self._membership("manager-a", self.tenant_a, TenantMembership.BaseRole.ADMIN_MANAGER)
        self.manager_b = self._membership("manager-b", self.tenant_b, TenantMembership.BaseRole.ADMIN_MANAGER)
        self.tech_a = self._membership("tech-a", self.tenant_a, TenantMembership.BaseRole.TECHNICIAN)
        self.tech_a2 = self._membership("tech-a2", self.tenant_a, TenantMembership.BaseRole.TECHNICIAN)
        self.tech_b = self._membership("tech-b", self.tenant_b, TenantMembership.BaseRole.TECHNICIAN)
        self.sales_a = self._membership("sales-a", self.tenant_a, TenantMembership.BaseRole.SALES)
        self.customer_a = Customer.objects.create(
            tenant=self.tenant_a, organization=self.tenant_a, name="Customer A",
            customer_type="internet", location="Arusha",
        )
        self.customer_b = Customer.objects.create(
            tenant=self.tenant_b, organization=self.tenant_b, name="Secret Customer B",
            customer_type="internet", location="Mwanza",
        )
        self.report_a = self._report(self.tech_a, self.customer_a, title="Antenna alignment")
        self.report_a2 = self._report(self.tech_a2, self.customer_a, title="Router replacement")
        self.report_b = self._report(self.tech_b, self.customer_b, title="Tenant B private work")

    def _membership(self, username, tenant, role):
        user = User.objects.create_user(username=username, password="test-pass-123")
        return TenantMembership.objects.create(tenant=tenant, user=user, base_role=role)

    def _report(self, technician, customer, *, title, status=TechnicianWorkReport.Status.DRAFT):
        return TechnicianWorkReport.objects.create(
            tenant=technician.tenant, technician=technician, customer=customer,
            work_title=title, client_name=customer.name, service_date=date(2026, 8, 1),
            work_location="Client site", activity_description="Completed technical work.",
            agreed_amount=Decimal("987654.32"), status=status,
        )

    def _client_for(self, membership):
        client = Client()
        self.assertTrue(client.login(username=membership.user.username, password="test-pass-123"))
        return client

    def test_role_permissions_match_confirmed_boundaries(self):
        self.assertTrue(has_tenant_permission(
            self.tech_a.user, self.tenant_a, PermissionCode.TECHNICIAN_WORK_REPORTS_CREATE_OWN,
            membership=self.tech_a,
        ))
        self.assertFalse(has_tenant_permission(
            self.tech_a.user, self.tenant_a, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_ALL,
            membership=self.tech_a,
        ))
        self.assertTrue(has_tenant_permission(
            self.manager_a.user, self.tenant_a, PermissionCode.TECHNICIAN_WORK_REPORTS_APPROVE,
            membership=self.manager_a,
        ))
        self.assertFalse(has_tenant_permission(
            self.sales_a.user, self.tenant_a, PermissionCode.TECHNICIAN_WORK_REPORTS_VIEW_OWN,
            membership=self.sales_a,
        ))

    def test_technician_list_and_detail_are_owner_scoped(self):
        client = self._client_for(self.tech_a)
        response = client.get(reverse("work_reports:list"))
        self.assertContains(response, self.report_a.work_title)
        self.assertNotContains(response, self.report_a2.work_title)
        self.assertNotContains(response, self.report_b.work_title)
        self.assertEqual(client.get(reverse("work_reports:detail", args=[self.report_a2.pk])).status_code, 404)
        self.assertEqual(client.get(reverse("work_reports:detail", args=[self.report_b.pk])).status_code, 404)

    def test_manager_cannot_review_another_tenant_report_by_id(self):
        client = self._client_for(self.manager_a)
        self.assertEqual(client.get(reverse("work_reports:detail", args=[self.report_b.pk])).status_code, 404)
        response = client.post(reverse("work_reports:approve", args=[self.report_b.pk]))
        self.assertEqual(response.status_code, 403)
        self.report_b.refresh_from_db()
        self.assertEqual(self.report_b.status, TechnicianWorkReport.Status.DRAFT)

    def test_create_forces_tenant_owner_and_rejects_cross_tenant_customer(self):
        client = self._client_for(self.tech_a)
        response = client.get(reverse("work_reports:create"))
        self.assertNotContains(response, self.customer_b.name)
        payload = {
            "work_title": "Cable repair", "client_name": "Client",
            "customer": self.customer_b.pk, "service_date": "2026-08-02",
            "work_location": "Site", "activity_description": "Repaired cable",
            "agreed_amount": "10000.00", "internal_notes": "",
            "tenant": self.tenant_b.pk, "technician": self.tech_b.pk,
        }
        response = client.post(reverse("work_reports:create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TechnicianWorkReport.objects.unscoped().filter(work_title="Cable repair").exists())
        payload["customer"] = self.customer_a.pk
        response = client.post(reverse("work_reports:create"), payload)
        self.assertEqual(response.status_code, 302)
        created = TechnicianWorkReport.objects.unscoped().get(work_title="Cable repair")
        self.assertEqual(created.tenant_id, self.tenant_a.pk)
        self.assertEqual(created.technician_id, self.tech_a.pk)

    def test_draft_rejected_resubmitted_approved_lifecycle(self):
        submit_report(report_id=self.report_a.pk, membership=self.tech_a)
        self.report_a.refresh_from_db()
        self.assertEqual(self.report_a.status, TechnicianWorkReport.Status.SUBMITTED)
        reject_report(report_id=self.report_a.pk, membership=self.manager_a, reason="Add the router serial number.")
        self.report_a.refresh_from_db()
        self.assertEqual(self.report_a.status, TechnicianWorkReport.Status.REJECTED)
        self.assertEqual(self.report_a.rejection_reason, "Add the router serial number.")
        self.report_a.internal_notes = "Router SN-001"
        self.report_a.save()
        submit_report(report_id=self.report_a.pk, membership=self.tech_a)
        approve_report(report_id=self.report_a.pk, membership=self.manager_a)
        self.report_a.refresh_from_db()
        self.assertEqual(self.report_a.status, TechnicianWorkReport.Status.APPROVED)
        self.assertEqual(self.report_a.approved_by_id, self.manager_a.pk)
        self.assertEqual(
            list(self.report_a.history.order_by("created_at", "id").values_list("event", flat=True)),
            ["SUBMITTED", "REJECTED", "RESUBMITTED", "APPROVED"],
        )

    def test_rejection_requires_reason(self):
        submit_report(report_id=self.report_a.pk, membership=self.tech_a)
        with self.assertRaises(ValidationError):
            reject_report(report_id=self.report_a.pk, membership=self.manager_a, reason="  ")
        self.report_a.refresh_from_db()
        self.assertEqual(self.report_a.status, TechnicianWorkReport.Status.SUBMITTED)

    def test_approved_report_is_immutable_except_audited_manager_correction(self):
        submit_report(report_id=self.report_a.pk, membership=self.tech_a)
        approve_report(report_id=self.report_a.pk, membership=self.manager_a)
        self.report_a.refresh_from_db()
        self.report_a.agreed_amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            self.report_a.save()
        corrected = correct_approved_report(
            report_id=self.report_a.pk, membership=self.manager_a,
            cleaned_data={"agreed_amount": Decimal("900000.00")},
            reason="Corrected the transposed agreed amount.",
        )
        self.assertEqual(corrected.status, TechnicianWorkReport.Status.APPROVED)
        event = corrected.history.get(event=WorkReportHistory.Event.CORRECTED)
        self.assertEqual(event.reason, "Corrected the transposed agreed amount.")
        self.assertEqual(event.snapshot["before"]["agreed_amount"], "987654.32")
        self.assertEqual(event.snapshot["after"]["agreed_amount"], "900000.00")
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            TechnicianWorkReport.objects.unscoped().filter(pk=corrected.pk).update(
                agreed_amount=Decimal("2.00"),
            )
        with self.assertRaises(ValidationError):
            TechnicianWorkReport.objects.unscoped().filter(pk=corrected.pk).delete()

    def test_sales_and_technician_cannot_access_approval_or_customer_finance_surfaces(self):
        for membership in (self.sales_a, self.tech_a):
            client = self._client_for(membership)
            self.assertIn(client.get(reverse("work_reports:approval_queue")).status_code, {403, 404})
        tech_client = self._client_for(self.tech_a)
        self.assertEqual(tech_client.get(reverse("customer-list")).status_code, 403)

    def test_agreed_amount_does_not_appear_on_customer_list(self):
        client = self._client_for(self.manager_a)
        response = client.get(reverse("customer-list"))
        self.assertNotContains(response, "987654.32")
