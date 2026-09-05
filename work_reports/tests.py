from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.utils import timezone
from django.urls import reverse

from audit.models import AuditLog
from billing.models import BillingDocument
from customers.models import Customer
from inventory.models import StockMovement, SupplierPaymentRecord
from users.models import Organization, TenantMembership
from users.permissions import PermissionCode, has_tenant_permission

from .models import (
    TechnicianPaymentRecord, TechnicianWorkReport, WorkReportHistory,
    WorkReportServiceDay,
)
from .services import (
    approve_report, confirm_technician_payment, correct_approved_report,
    create_report,
    dispute_technician_payment, record_technician_payment, reject_report,
    replace_technician_payment, submit_report, void_technician_payment,
    update_own_report,
)


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
        report = TechnicianWorkReport.objects.create(
            tenant=technician.tenant, technician=technician, customer=customer,
            work_title=title, client_name=customer.name, service_date=date(2026, 8, 1),
            work_location="Client site", activity_description="Completed technical work.",
            agreed_amount=Decimal("987654.32"), status=status,
        )
        WorkReportServiceDay.objects.create(
            tenant=technician.tenant, report=report, service_date=report.service_date,
        )
        return report

    def _work_date_payload(self, *rows):
        payload = {
            "work_dates-TOTAL_FORMS": str(len(rows)),
            "work_dates-INITIAL_FORMS": "0",
            "work_dates-MIN_NUM_FORMS": "0",
            "work_dates-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
            service_date, *note = row
            payload[f"work_dates-{index}-service_date"] = service_date
            payload[f"work_dates-{index}-activity_note"] = note[0] if note else ""
        return payload

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
            "customer": self.customer_b.pk, "service_date": "2025-01-01",
            "work_location": "Site", "activity_description": "Repaired cable",
            "agreed_amount": "10000.00", "internal_notes": "",
            "tenant": self.tenant_b.pk, "technician": self.tech_b.pk,
        }
        payload.update(self._work_date_payload(("2026-08-02",)))
        response = client.post(reverse("work_reports:create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TechnicianWorkReport.objects.unscoped().filter(work_title="Cable repair").exists())
        payload["customer"] = self.customer_a.pk
        response = client.post(reverse("work_reports:create"), payload)
        self.assertEqual(response.status_code, 302)
        created = TechnicianWorkReport.objects.unscoped().get(work_title="Cable repair")
        self.assertEqual(created.tenant_id, self.tenant_a.pk)
        self.assertEqual(created.technician_id, self.tech_a.pk)
        self.assertEqual(created.service_date, date(2026, 8, 2))

    def test_client_name_is_optional_through_the_full_report_lifecycle(self):
        client = self._client_for(self.tech_a)
        payload = {
            "work_title": "Preventative network maintenance",
            "client_name": "",
            "customer": "",
            "service_date": "2026-08-02",
            "work_location": "Core network cabinet",
            "activity_description": "Completed scheduled preventative maintenance.",
            "agreed_amount": "10000.00",
            "internal_notes": "",
        }
        payload.update(self._work_date_payload(("2026-08-02",)))

        response = client.post(reverse("work_reports:create"), payload)

        self.assertEqual(response.status_code, 302)
        report = TechnicianWorkReport.objects.unscoped().get(
            work_title="Preventative network maintenance",
        )
        self.assertEqual(report.client_name, "")
        self.assertIsNone(report.customer_id)
        submit_report(report_id=report.pk, membership=self.tech_a)
        approve_report(report_id=report.pk, membership=self.manager_a)
        report.refresh_from_db()
        self.assertEqual(report.status, TechnicianWorkReport.Status.APPROVED)

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

    def _create_with_dates(self, dates):
        return create_report(
            membership=self.tech_a,
            cleaned_data={
                "work_title": "Multi-day installation",
                "client_name": "Customer A",
                "customer": self.customer_a,
                "work_location": "Client site",
                "activity_description": "Completed the complete installation.",
                "agreed_amount": Decimal("150000.00"),
                "internal_notes": "",
            },
            service_days=[
                {"service_date": value, "activity_note": note}
                for value, note in dates
            ],
        )

    def test_create_report_with_one_work_date(self):
        report = self._create_with_dates([(date(2026, 9, 2), "Installation started")])
        self.assertEqual(report.service_date, date(2026, 9, 2))
        self.assertEqual(
            list(report.service_days.values_list("service_date", "activity_note")),
            [(date(2026, 9, 2), "Installation started")],
        )

    def test_create_report_with_consecutive_dates(self):
        report = self._create_with_dates([
            (date(2026, 9, 3), "Testing"),
            (date(2026, 9, 2), "Installation"),
        ])
        self.assertEqual(report.service_days_summary, "02–03 Sep 2026 · 2 days")
        self.assertEqual(report.service_date, date(2026, 9, 2))

    def test_create_report_with_non_consecutive_dates_and_chronological_display(self):
        report = self._create_with_dates([
            (date(2026, 8, 7), "Final test"),
            (date(2026, 8, 2), "Installation"),
            (date(2026, 8, 4), "Router configured"),
        ])
        self.assertEqual(report.service_days_summary, "02, 04 & 07 Aug 2026 · 3 days")
        response = self._client_for(self.tech_a).get(
            reverse("work_reports:detail", args=[report.pk]),
        )
        self.assertContains(response, "Router configured")
        content = response.content.decode().split(
            "Only the explicitly recorded work days are shown.", 1,
        )[1]
        self.assertLess(content.index("02 Aug 2026"), content.index("04 Aug 2026"))
        self.assertLess(content.index("04 Aug 2026"), content.index("07 Aug 2026"))

    def test_work_date_formset_requires_one_date_and_rejects_duplicates_and_future(self):
        client = self._client_for(self.tech_a)
        base = {
            "work_title": "Validation test",
            "client_name": "",
            "customer": "",
            "work_location": "Site",
            "activity_description": "Completed work.",
            "agreed_amount": "100.00",
            "internal_notes": "",
        }
        missing = {**base, **self._work_date_payload(("",))}
        response = client.post(reverse("work_reports:create"), missing)
        self.assertContains(response, "Add at least one work date.")
        duplicate = {
            **base,
            **self._work_date_payload(("2026-09-02",), ("2026-09-02",)),
        }
        response = client.post(reverse("work_reports:create"), duplicate)
        self.assertContains(response, "This work date is already listed.")
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = client.post(
            reverse("work_reports:create"),
            {**base, **self._work_date_payload((future,))},
        )
        self.assertContains(response, "Work dates cannot be in the future.")
        self.assertFalse(TechnicianWorkReport.objects.unscoped().filter(
            work_title="Validation test",
        ).exists())

    def test_technician_can_edit_dates_on_own_draft_and_after_rejection(self):
        update_own_report(
            report_id=self.report_a.pk,
            membership=self.tech_a,
            cleaned_data={"work_title": self.report_a.work_title},
            service_days=[
                {"service_date": date(2026, 8, 3), "activity_note": "Second visit"},
                {"service_date": date(2026, 8, 2), "activity_note": "First visit"},
            ],
        )
        self.report_a.refresh_from_db()
        self.assertEqual(self.report_a.service_date, date(2026, 8, 2))
        submit_report(report_id=self.report_a.pk, membership=self.tech_a)
        reject_report(
            report_id=self.report_a.pk, membership=self.manager_a,
            reason="Correct the visit dates.",
        )
        update_own_report(
            report_id=self.report_a.pk,
            membership=self.tech_a,
            cleaned_data={},
            service_days=[
                {"service_date": date(2026, 8, 4), "activity_note": "Corrected"},
            ],
        )
        self.assertEqual(
            list(self.report_a.service_days.values_list("service_date", flat=True)),
            [date(2026, 8, 4)],
        )

    def test_submitted_and_approved_work_dates_are_immutable_outside_services(self):
        day = self.report_a.service_days.get()
        submit_report(report_id=self.report_a.pk, membership=self.tech_a)
        day.activity_note = "Bypass"
        with self.assertRaises(ValidationError):
            day.save()
        with self.assertRaises(ValidationError):
            update_own_report(
                report_id=self.report_a.pk,
                membership=self.tech_a,
                cleaned_data={},
                service_days=[{"service_date": date(2026, 8, 5), "activity_note": ""}],
            )
        approve_report(report_id=self.report_a.pk, membership=self.manager_a)
        blocked_new_day = WorkReportServiceDay(
            tenant=self.tenant_a,
            report=self.report_a,
            service_date=date(2026, 8, 6),
        )
        with self.assertRaises(ValidationError):
            blocked_new_day.save()
        with self.assertRaises(ValidationError):
            day.delete()
        with self.assertRaises(ValidationError):
            WorkReportServiceDay.objects.unscoped().filter(pk=day.pk).update(
                activity_note="Bypass",
            )
        with self.assertRaises(ValidationError):
            WorkReportServiceDay.objects.unscoped().filter(pk=day.pk).delete()

    def test_approved_correction_audits_complete_work_dates_without_status_changes(self):
        submit_report(report_id=self.report_a.pk, membership=self.tech_a)
        approved = approve_report(report_id=self.report_a.pk, membership=self.manager_a)
        approved_at = approved.approved_at
        approved_by_id = approved.approved_by_id
        corrected = correct_approved_report(
            report_id=approved.pk,
            membership=self.manager_a,
            cleaned_data={"work_location": "Corrected site"},
            service_days=[
                {"service_date": date(2026, 8, 4), "activity_note": "Testing"},
                {"service_date": date(2026, 8, 2), "activity_note": "Installation"},
            ],
            reason="Corrected the documented visits.",
        )
        event = corrected.history.get(event=WorkReportHistory.Event.CORRECTED)
        self.assertEqual(event.snapshot["before"]["service_days"][0]["service_date"], "2026-08-01")
        self.assertEqual(
            [row["service_date"] for row in event.snapshot["after"]["service_days"]],
            ["2026-08-02", "2026-08-04"],
        )
        self.assertEqual(corrected.status, TechnicianWorkReport.Status.APPROVED)
        self.assertEqual(corrected.approved_at, approved_at)
        self.assertEqual(corrected.approved_by_id, approved_by_id)

    def test_cross_tenant_work_date_is_rejected(self):
        day = WorkReportServiceDay(
            tenant=self.tenant_b,
            report=self.report_a,
            service_date=date(2026, 8, 8),
        )
        with self.assertRaises(ValidationError):
            day.save()

    def test_legacy_snapshot_without_service_days_and_parent_date_fallback_render(self):
        old_event = WorkReportHistory.objects.create(
            tenant=self.tenant_a,
            report=self.report_a,
            actor_membership=self.tech_a,
            event=WorkReportHistory.Event.CREATED,
            snapshot={"service_date": "2026-08-01"},
        )
        self.assertNotIn("service_days", old_event.snapshot)
        response = self._client_for(self.tech_a).get(
            reverse("work_reports:detail", args=[self.report_a.pk]),
        )
        self.assertEqual(response.status_code, 200)

    def test_list_and_approval_queue_prefetch_service_days(self):
        technician_response = self._client_for(self.tech_a).get(reverse("work_reports:list"))
        self.assertIn("service_days", technician_response.context["reports"]._prefetch_related_lookups[0].prefetch_through)
        submit_report(report_id=self.report_a.pk, membership=self.tech_a)
        manager_response = self._client_for(self.manager_a).get(
            reverse("work_reports:approval_queue"),
        )
        self.assertIn("service_days", manager_response.context["reports"]._prefetch_related_lookups[0].prefetch_through)

    def test_service_days_have_no_amount_field(self):
        self.assertNotIn("agreed_amount", {
            field.name for field in WorkReportServiceDay._meta.fields
        })

    def _approve(self, report=None):
        report = report or self.report_a
        submit_report(report_id=report.pk, membership=report.technician)
        manager = self.manager_a if report.tenant_id == self.tenant_a.pk else self.manager_b
        return approve_report(report_id=report.pk, membership=manager)

    def _record_payment(self, report=None, manager=None, **overrides):
        report = report or self.report_a
        report.refresh_from_db()
        if report.status != TechnicianWorkReport.Status.APPROVED:
            report = self._approve(report)
        values = {
            "report_id": report.pk,
            "membership": manager or self.manager_a,
            "amount_paid": report.agreed_amount,
            "payment_date": date(2026, 8, 10),
            "payment_method": TechnicianPaymentRecord.PaymentMethod.CASH,
        }
        values.update(overrides)
        return record_technician_payment(**values)

    def test_payment_requires_approved_report_and_active_technician(self):
        with self.assertRaises(ValidationError):
            record_technician_payment(
                report_id=self.report_a.pk,
                membership=self.manager_a,
                amount_paid=self.report_a.agreed_amount,
                payment_date=date(2026, 8, 10),
                payment_method=TechnicianPaymentRecord.PaymentMethod.CASH,
            )
        self._approve()
        self.tech_a.is_active = False
        self.tech_a.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            self._record_payment()

    def test_only_same_tenant_manager_can_record_payment(self):
        self._approve()
        for actor, exception in (
            (self.manager_b, PermissionDenied),
            (self.sales_a, PermissionDenied),
            (self.tech_a, PermissionDenied),
        ):
            with self.assertRaises(exception):
                self._record_payment(manager=actor)
        payment = self._record_payment()
        self.assertEqual(payment.tenant_id, self.tenant_a.pk)
        self.assertEqual(payment.recorded_by_id, self.manager_a.pk)

    def test_record_view_ignores_ownership_and_audit_fields_from_post(self):
        self._approve()
        client = self._client_for(self.manager_a)
        page = client.get(reverse("work_reports:payment_record", args=[self.report_a.pk]))
        self.assertNotContains(page, "confirm_manual_record")
        response = client.post(reverse("work_reports:payment_record", args=[self.report_a.pk]), {
            "amount_paid": "987654.32",
            "payment_date": "2026-08-10",
            "payment_method": "CASH",
            "reference": "MANUAL-1",
            "manager_note": "Paid at office",
            "adjustment_reason": "",
            "tenant": self.tenant_b.pk,
            "report": self.report_b.pk,
            "technician": self.tech_b.pk,
            "recorded_by": self.manager_b.pk,
            "status": "CONFIRMED",
            "confirmed_at": "2026-08-10T12:00",
        })
        self.assertEqual(response.status_code, 302)
        payment = TechnicianPaymentRecord.objects.unscoped().get()
        self.assertEqual(payment.tenant_id, self.tenant_a.pk)
        self.assertEqual(payment.report_id, self.report_a.pk)
        self.assertEqual(payment.report.technician_id, self.tech_a.pk)
        self.assertEqual(payment.recorded_by_id, self.manager_a.pk)
        self.assertEqual(payment.status, TechnicianPaymentRecord.Status.AWAITING_CONFIRMATION)

    def test_snapshot_equal_and_adjusted_amount_rules(self):
        self._approve()
        payment = self._record_payment()
        self.assertEqual(payment.agreed_amount_snapshot, Decimal("987654.32"))
        self.assertEqual(payment.adjustment_reason, "")
        void_technician_payment(
            payment_id=payment.pk, membership=self.manager_a,
            reason="Correcting the final amount.",
        )
        with self.assertRaises(ValidationError):
            self._record_payment(amount_paid=Decimal("900000.00"))
        with self.assertRaises(ValidationError):
            self._record_payment(
                amount_paid=Decimal("900000.00"), adjustment_reason="Approved reduction",
            )
        adjusted = self._record_payment(
            amount_paid=Decimal("900000.00"),
            adjustment_reason="Approved final scope reduction.",
            confirm_adjusted_amount=True,
            replaces_id=payment.pk,
        )
        self.report_a.refresh_from_db()
        self.assertEqual(self.report_a.agreed_amount, Decimal("987654.32"))
        self.assertEqual(adjusted.difference, Decimal("-87654.32"))

    def test_duplicate_active_payment_is_blocked_by_service_and_constraint(self):
        self._approve()
        self._record_payment()
        with self.assertRaises(ValidationError):
            self._record_payment()
        names = {constraint.name for constraint in TechnicianPaymentRecord._meta.constraints}
        self.assertIn("one_active_technician_payment_per_report", names)
        self.assertEqual(
            TechnicianPaymentRecord.objects.unscoped().exclude(
                status=TechnicianPaymentRecord.Status.VOIDED,
            ).filter(report=self.report_a).count(),
            1,
        )

    def test_payment_detail_and_actions_are_owner_and_tenant_scoped(self):
        payment = self._record_payment()
        owner = self._client_for(self.tech_a)
        self.assertContains(owner.get(reverse("work_reports:detail", args=[self.report_a.pk])), "Confirm received")
        for actor in (self.tech_a2, self.tech_b, self.sales_a):
            client = self._client_for(actor)
            self.assertEqual(client.post(reverse("work_reports:payment_confirm", args=[payment.pk])).status_code, 404)
            self.assertEqual(client.get(reverse("work_reports:payment_dispute", args=[payment.pk])).status_code, 404)
        manager = self._client_for(self.manager_a)
        self.assertEqual(manager.post(reverse("work_reports:payment_confirm", args=[payment.pk])).status_code, 403)
        cross_manager = self._client_for(self.manager_b)
        self.assertEqual(cross_manager.get(reverse("work_reports:payment_void", args=[payment.pk])).status_code, 404)

    def test_confirmation_is_one_way_and_cannot_be_repeated_or_disputed(self):
        payment = self._record_payment()
        confirmed = confirm_technician_payment(payment_id=payment.pk, membership=self.tech_a)
        self.assertEqual(confirmed.status, TechnicianPaymentRecord.Status.CONFIRMED)
        self.assertIsNotNone(confirmed.confirmed_at)
        with self.assertRaises(ValidationError):
            confirm_technician_payment(payment_id=payment.pk, membership=self.tech_a)
        with self.assertRaises(ValidationError):
            dispute_technician_payment(
                payment_id=payment.pk, membership=self.tech_a, reason="Amount not received.",
            )

    def test_dispute_requires_reason_and_is_one_way(self):
        payment = self._record_payment()
        with self.assertRaises(ValidationError):
            dispute_technician_payment(
                payment_id=payment.pk, membership=self.tech_a, reason="   ",
            )
        disputed = dispute_technician_payment(
            payment_id=payment.pk, membership=self.tech_a,
            reason="The recorded amount was not received.",
        )
        self.assertEqual(disputed.status, TechnicianPaymentRecord.Status.DISPUTED)
        self.assertIsNotNone(disputed.disputed_at)
        with self.assertRaises(ValidationError):
            dispute_technician_payment(
                payment_id=payment.pk, membership=self.tech_a, reason="Second response.",
            )

    def test_void_requires_reason_preserves_history_and_allows_one_replacement(self):
        payment = self._record_payment()
        dispute_technician_payment(
            payment_id=payment.pk, membership=self.tech_a,
            reason="The amount was not received.",
        )
        with self.assertRaises(ValidationError):
            void_technician_payment(payment_id=payment.pk, membership=self.manager_a, reason=" ")
        voided = void_technician_payment(
            payment_id=payment.pk, membership=self.manager_a,
            reason="Recorded against the wrong transfer reference.",
        )
        replacement = replace_technician_payment(
            voided_payment_id=voided.pk,
            membership=self.manager_a,
            amount_paid=voided.agreed_amount_snapshot,
            payment_date=date(2026, 8, 11),
            payment_method=TechnicianPaymentRecord.PaymentMethod.BANK_TRANSFER,
        )
        self.assertEqual(replacement.replaces_id, voided.pk)
        self.assertTrue(TechnicianPaymentRecord.objects.unscoped().filter(pk=voided.pk).exists())
        with self.assertRaises(ValidationError):
            replace_technician_payment(
                voided_payment_id=voided.pk,
                membership=self.manager_a,
                amount_paid=voided.agreed_amount_snapshot,
                payment_date=date(2026, 8, 12),
                payment_method=TechnicianPaymentRecord.PaymentMethod.CASH,
            )
        events = set(self.report_a.history.values_list("event", flat=True))
        self.assertTrue({
            WorkReportHistory.Event.PAYMENT_RECORDED,
            WorkReportHistory.Event.PAYMENT_DISPUTED,
            WorkReportHistory.Event.PAYMENT_VOIDED,
            WorkReportHistory.Event.PAYMENT_REPLACED,
        }.issubset(events))

    def test_payment_records_and_history_are_immutable(self):
        payment = self._record_payment()
        payment.amount_paid = Decimal("1.00")
        with self.assertRaises(ValidationError):
            payment.save()
        with self.assertRaises(ValidationError):
            TechnicianPaymentRecord.objects.unscoped().filter(pk=payment.pk).update(
                amount_paid=Decimal("2.00"),
            )
        with self.assertRaises(ValidationError):
            TechnicianPaymentRecord.objects.unscoped().filter(pk=payment.pk).delete()
        with self.assertRaises(ValidationError):
            payment.delete()

    def test_every_payment_event_creates_history_and_audit(self):
        payment = self._record_payment(
            amount_paid=Decimal("900000.00"),
            adjustment_reason="Approved final scope reduction.",
            confirm_adjusted_amount=True,
        )
        confirm_technician_payment(payment_id=payment.pk, membership=self.tech_a)
        history_events = set(self.report_a.history.values_list("event", flat=True))
        self.assertIn(WorkReportHistory.Event.PAYMENT_RECORDED, history_events)
        self.assertIn(WorkReportHistory.Event.PAYMENT_ADJUSTMENT_APPROVED, history_events)
        self.assertIn(WorkReportHistory.Event.PAYMENT_CONFIRMED, history_events)
        audit_events = set(AuditLog.objects.filter(
            object_type="TechnicianPaymentRecord", object_id=str(payment.pk),
        ).values_list("action", flat=True))
        self.assertEqual(len(audit_events), 3)

    def test_payment_workflow_has_no_billing_supplier_or_inventory_side_effects(self):
        before = (
            BillingDocument.objects.unscoped().count(),
            SupplierPaymentRecord.objects.unscoped().count(),
            StockMovement.objects.unscoped().count(),
        )
        payment = self._record_payment()
        confirm_technician_payment(payment_id=payment.pk, membership=self.tech_a)
        after = (
            BillingDocument.objects.unscoped().count(),
            SupplierPaymentRecord.objects.unscoped().count(),
            StockMovement.objects.unscoped().count(),
        )
        self.assertEqual(after, before)

    def test_agreed_amount_correction_is_blocked_after_payment_history_exists(self):
        self._record_payment()
        with self.assertRaises(ValidationError):
            correct_approved_report(
                report_id=self.report_a.pk,
                membership=self.manager_a,
                cleaned_data={"agreed_amount": Decimal("1.00")},
                reason="Attempted correction after payment.",
            )
