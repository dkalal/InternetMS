from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.db import close_old_connections, connection, transaction, IntegrityError
from django.test import TestCase, TransactionTestCase, Client
from django.urls import reverse
from django.utils import timezone

from users.models import Organization, TenantMembership as M, SupportAccessSession
from customers.models import Customer
from .assignment_services import save_assignment, manager_action, respond, create_assignment_report, complete_assignment
from .models import WorkAssignment as A, WorkAssignmentHistory, TechnicianWorkReport as R
from .services import submit_report, approve_report, reject_report, update_own_report, correct_approved_report


class AssignmentFixtures:
    def setUp(self):
        self.tenant = Organization.objects.create(name="Synthetic assignment tenant", slug="assignment-tenant")
        self.other = Organization.objects.create(name="Other synthetic tenant", slug="assignment-other")
        self.manager = self.member("assignment-manager", M.BaseRole.ADMIN_MANAGER)
        self.tech = self.member("assignment-tech", M.BaseRole.TECHNICIAN)
        self.tech2 = self.member("assignment-tech2", M.BaseRole.TECHNICIAN)
        self.sales = self.member("assignment-sales", M.BaseRole.SALES)
        self.foreign = self.member("assignment-foreign", M.BaseRole.TECHNICIAN, self.other)
        self.data = dict(
            technician=self.tech, work_title="Synthetic CCTV installation", scope="Install four synthetic cameras",
            proposed_start=timezone.localdate() + timedelta(days=2), proposed_end=None,
            proposed_amount=Decimal("100000.00"), management_notes="PRIVATE-MANAGEMENT-ONLY",
        )

    def member(self, username, role, tenant=None):
        user = get_user_model().objects.create_user(username=username)
        return M.objects.create(tenant=tenant or self.tenant, user=user, base_role=role)

    def draft(self, **overrides):
        return save_assignment(membership=self.manager, cleaned_data={**self.data, **overrides})

    def creation_token(self):
        return signing.dumps({"tenant": self.tenant.pk, "creator": self.manager.pk, "key": str(uuid4())}, salt="work-assignment-create")

    def offer(self):
        assignment = self.draft()
        return manager_action(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, action="send")

    def response(self, assignment, action="accept", **kwargs):
        return respond(assignment_id=assignment.pk, membership=self.tech, version=assignment.version, revision=assignment.revision, action=action, **kwargs)

    def report(self, assignment, **overrides):
        return create_assignment_report(
            assignment_id=assignment.pk, membership=self.tech,
            cleaned_data={"activity_description": "Actual completed work", "agreed_amount": Decimal("1"), **overrides},
            service_days=[{"service_date": timezone.localdate()}],
        )

    def client_for(self, member):
        client = Client()
        client.force_login(member.user)
        return client


class AssignmentTests(AssignmentFixtures, TestCase):
    def test_completion_boundary_requires_authorization_and_stored_approval(self):
        assignment = self.response(self.offer())
        report = self.report(assignment)
        with self.assertRaises(PermissionDenied):
            complete_assignment(report, self.tech)
        report.status = "APPROVED"
        with self.assertRaises(ValidationError):
            complete_assignment(report, self.manager)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "ACCEPTED")

    def test_repeated_draft_creation_returns_existing_and_token_is_bound(self):
        token = self.creation_token()
        first = save_assignment(membership=self.manager, cleaned_data=self.data, request_token=token)
        second = save_assignment(membership=self.manager, cleaned_data=self.data, request_token=token)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(A.objects.unscoped().count(), 1)
        with self.assertRaises(ValidationError):
            save_assignment(membership=self.manager, cleaned_data=self.data, request_token=token + "tampered")
        other_manager = self.member("assignment-other-manager", M.BaseRole.ADMIN_MANAGER)
        with self.assertRaises(PermissionDenied):
            save_assignment(membership=other_manager, cleaned_data=self.data, request_token=token)

    def test_accept_is_not_payable_and_dates_can_be_future(self):
        assignment = self.response(self.offer())
        self.assertEqual(assignment.status, A.Status.ACCEPTED)
        self.assertEqual(assignment.accepted_revision, 1)
        self.assertFalse(R.objects.unscoped().filter(assignment=assignment).exists())
        self.assertEqual(assignment.accepted_terms["proposed_amount"], "100000.00")

    def test_change_request_preserves_offer_and_new_revision(self):
        assignment = self.response(self.offer(), "request_changes", reason="Additional work", proposed_amount=Decimal("150000"))
        self.assertEqual(assignment.proposed_amount, Decimal("100000"))
        assignment = save_assignment(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, cleaned_data={"proposed_amount": Decimal("150000")})
        assignment = manager_action(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, action="send")
        self.assertEqual(assignment.revision, 2)
        assignment = self.response(assignment)
        self.assertEqual(Decimal(assignment.accepted_terms["proposed_amount"]), Decimal("150000"))

    def test_stale_response_rejected_without_history(self):
        assignment = self.offer()
        save_assignment(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, cleaned_data={"scope": "Revised scope"})
        count = WorkAssignmentHistory.objects.unscoped().count()
        with self.assertRaises(ValidationError):
            self.response(assignment)
        self.assertEqual(WorkAssignmentHistory.objects.unscoped().count(), count)

    def test_invalid_responses_and_required_reasons(self):
        assignment = self.offer()
        for action in ("decline", "request_changes", "approve", "cancel"):
            with self.subTest(action=action), self.assertRaises(ValidationError):
                self.response(assignment, action)
        self.assertEqual(self.response(assignment, "decline", reason="Unavailable").status, "DECLINED")

    def test_reassignment_requires_reason_and_new_acceptance(self):
        assignment = self.response(self.offer())
        old_terms = assignment.accepted_terms.copy()
        with self.assertRaises(ValidationError):
            save_assignment(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, cleaned_data={"technician": self.tech2})
        assignment = save_assignment(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, cleaned_data={"technician": self.tech2}, reason="Availability changed")
        self.assertEqual(assignment.accepted_terms, old_terms)
        assignment = manager_action(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, action="send")
        with self.assertRaises(PermissionDenied):
            self.response(assignment)
        result = respond(assignment_id=assignment.pk, membership=self.tech2, version=assignment.version, revision=assignment.revision, action="accept")
        self.assertEqual(result.accepted_terms["technician_id"], self.tech2.pk)

    def test_report_is_unique_payment_trusted_and_completed_only_on_approval(self):
        assignment = self.response(self.offer())
        report = self.report(assignment)
        self.assertEqual(report.agreed_amount, Decimal("100000"))
        self.assertEqual(self.report(assignment).pk, report.pk)
        submit_report(report_id=report.pk, membership=self.tech)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "ACCEPTED")
        approve_report(report_id=report.pk, membership=self.manager)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "COMPLETED")

    def test_report_corrections_preserve_agreement(self):
        assignment = self.response(self.offer())
        report = self.report(assignment)
        submit_report(report_id=report.pk, membership=self.tech)
        reject_report(report_id=report.pk, membership=self.manager, reason="Add actual details")
        update_own_report(report_id=report.pk, membership=self.tech, cleaned_data={"activity_description": "Corrected work"})
        with self.assertRaises(ValidationError):
            update_own_report(report_id=report.pk, membership=self.tech, cleaned_data={"agreed_amount": Decimal("1")})
        submit_report(report_id=report.pk, membership=self.tech)
        approve_report(report_id=report.pk, membership=self.manager)
        with self.assertRaises(ValidationError):
            correct_approved_report(report_id=report.pk, membership=self.manager, cleaned_data={"agreed_amount": Decimal("1")}, reason="Attempted change")
        assignment.refresh_from_db()
        self.assertEqual(assignment.accepted_terms["proposed_amount"], "100000.00")

    def test_linked_assignment_blocks_management_mutations(self):
        assignment = self.response(self.offer())
        self.report(assignment)
        with self.assertRaises(ValidationError):
            save_assignment(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, cleaned_data={"scope": "Changed"})
        with self.assertRaises(ValidationError):
            manager_action(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, action="cancel", reason="Attempted cancellation")

    def test_future_actual_work_date_rejected(self):
        assignment = self.response(self.offer())
        with self.assertRaises(ValidationError):
            create_assignment_report(assignment_id=assignment.pk, membership=self.tech, cleaned_data={"activity_description": "Future work"}, service_days=[{"service_date": timezone.localdate() + timedelta(days=1)}])
        self.assertFalse(R.objects.unscoped().filter(assignment=assignment).exists())

    def test_authorization_cross_tenant_and_other_technician(self):
        assignment = self.offer()
        for actor in (self.sales, self.tech2, self.foreign):
            with self.subTest(actor=actor.pk), self.assertRaises(PermissionDenied):
                respond(assignment_id=assignment.pk, membership=actor, version=assignment.version, revision=1, action="accept")
        for actor in (self.tech, self.sales, self.foreign):
            with self.assertRaises(PermissionDenied):
                save_assignment(membership=actor, cleaned_data=self.data)
        with self.assertRaises(ValidationError):
            self.draft(technician=self.foreign)

    def test_inactive_user_and_membership_rechecked(self):
        assignment = self.offer()
        self.tech.user.is_active = False
        self.tech.user.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            self.response(assignment)
        self.tech.user.is_active = True
        self.tech.user.save(update_fields=["is_active"])
        M.objects.filter(pk=self.tech.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.response(assignment)

    def test_inactive_assignee_and_cross_tenant_customer_rejected(self):
        self.tech2.user.is_active = False
        self.tech2.user.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            self.draft(technician=self.tech2)
        customer = Customer.objects.create(tenant=self.other, organization=self.other, name="Synthetic foreign customer", customer_type="internet", location="Synthetic site")
        with self.assertRaises(ValidationError):
            self.draft(customer=customer)

    def test_database_enforces_one_report_per_assignment(self):
        assignment = self.response(self.offer())
        report = self.report(assignment)
        with self.assertRaises(IntegrityError), transaction.atomic():
            R.objects.bulk_create([R(
                tenant=self.tenant, technician=self.tech, assignment=assignment,
                work_title="Duplicate", activity_description="Duplicate", service_date=report.service_date,
                agreed_amount=report.agreed_amount,
            )])

    def test_stale_role_cannot_manage_or_respond(self):
        assignment = self.offer()
        M.objects.filter(pk=self.manager.pk).update(base_role=M.BaseRole.SALES)
        with self.assertRaises(PermissionDenied):
            manager_action(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, action="cancel", reason="Denied")

    def test_support_requires_explicit_session_and_cannot_manage(self):
        assignment = self.offer()
        support = self.member("assignment-support", M.BaseRole.SUPER_ADMIN, self.other)
        client = self.client_for(support)
        url = reverse("work_reports:assignment_detail", args=[assignment.pk])
        self.assertNotEqual(client.get(url).status_code, 200)
        access = SupportAccessSession.objects.create(
            actor=support.user, tenant=self.tenant, reason="Synthetic support verification",
            session_key=client.session.session_key,
        )
        session = client.session
        session["support_access_session_id"] = access.pk
        session.save()
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(client.get(reverse("work_reports:assignment_edit", args=[assignment.pk])).status_code, 403)

    def test_cancellation_is_terminal_and_audited(self):
        assignment = self.offer()
        with self.assertRaises(ValidationError):
            manager_action(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, action="cancel")
        assignment = manager_action(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, action="cancel", reason="No longer required")
        with self.assertRaises(ValidationError):
            self.response(assignment)
        with self.assertRaises(ValidationError):
            save_assignment(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, cleaned_data={})
        self.assertEqual(assignment.history.filter(event="CANCEL").count(), 1)

    def test_history_and_assignments_resist_uncontrolled_writes(self):
        assignment = self.offer()
        event = WorkAssignmentHistory.objects.unscoped().filter(assignment=assignment).first()
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            A.objects.unscoped().filter(pk=assignment.pk).update(status="ACCEPTED")
        assignment.scope = "Uncontrolled edit"
        with self.assertRaises(ValidationError):
            assignment.save()

    def test_failed_history_rolls_back_entire_action(self):
        assignment = self.offer()
        with patch("work_reports.assignment_services._history", side_effect=RuntimeError("Synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.response(assignment)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "OFFERED")
        self.assertIsNone(assignment.accepted_revision)

    def test_pages_scope_and_privacy(self):
        assignment = self.offer()
        url = reverse("work_reports:assignment_detail", args=[assignment.pk])
        for member in (self.tech2, self.foreign, self.sales):
            self.assertIn(self.client_for(member).get(url).status_code, (403, 404))
        response = self.client_for(self.tech).get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "PRIVATE-MANAGEMENT-ONLY")
        self.assertContains(self.client_for(self.manager).get(url), "PRIVATE-MANAGEMENT-ONLY")
        self.assertNotIn("management_notes", str(assignment.history.first().snapshot))

    def test_drafts_hidden_and_list_paginated(self):
        assignment = self.draft()
        client = self.client_for(self.tech)
        self.assertEqual(client.get(reverse("work_reports:assignment_detail", args=[assignment.pk])).status_code, 404)
        response = self.client_for(self.manager).get(reverse("work_reports:assignment_list"))
        self.assertEqual(response.context["page_obj"].paginator.per_page, 20)

    def test_response_post_and_stale_error_render(self):
        assignment = self.offer()
        url = reverse("work_reports:assignment_detail", args=[assignment.pk])
        payload = {"version": assignment.version, "revision": assignment.revision, "action": "accept"}
        client = self.client_for(self.tech)
        self.assertEqual(client.post(url, payload).status_code, 302)
        # A stale manager form must render an actionable validation error.
        manager = self.client_for(self.manager)
        response = manager.post(url, {**payload, "action": "cancel", "reason": "Cancelled"})
        self.assertContains(response, "Reload and review")

    def test_report_browser_post_ignores_tampered_amount(self):
        assignment = self.response(self.offer())
        url = reverse("work_reports:assignment_report", args=[assignment.pk])
        response = self.client_for(self.tech).post(url, {
            "work_title": "Malicious title", "agreed_amount": "1", "activity_description": "Actual work",
            "work_dates-TOTAL_FORMS": "1", "work_dates-INITIAL_FORMS": "0",
            "work_dates-0-service_date": timezone.localdate().isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        report = R.objects.unscoped().get(assignment=assignment)
        self.assertEqual(report.agreed_amount, Decimal("100000"))
        self.assertEqual(report.work_title, assignment.work_title)


class AssignmentConcurrencyTests(AssignmentFixtures, TransactionTestCase):
    def race(self, functions):
        self.assertEqual(connection.vendor, "postgresql", "These locking checks require PostgreSQL.")
        barrier = Barrier(2)
        def run(fn):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return fn()
                except ValidationError:
                    return "stale"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, functions))

    def test_concurrent_accept_and_cancel(self):
        assignment = self.offer()
        results = self.race([
            lambda: self.response(assignment).status,
            lambda: manager_action(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, action="cancel", reason="Cancelled").status,
        ])
        self.assertEqual(results.count("stale"), 1)
        assignment.refresh_from_db()
        self.assertIn(assignment.status, {"ACCEPTED", "CANCELLED"})

    def test_concurrent_accept_and_revise(self):
        assignment = self.offer()
        results = self.race([
            lambda: self.response(assignment).status,
            lambda: save_assignment(assignment_id=assignment.pk, membership=self.manager, version=assignment.version, cleaned_data={"scope": "Revised"}).status,
        ])
        self.assertEqual(results.count("stale"), 1)

    def test_concurrent_report_creation_returns_same_record(self):
        assignment = self.response(self.offer())
        results = self.race([lambda: self.report(assignment).pk, lambda: self.report(assignment).pk])
        self.assertEqual(results[0], results[1])
        self.assertEqual(R.objects.unscoped().filter(assignment=assignment).count(), 1)

    def test_concurrent_draft_creation_returns_same_record(self):
        token = self.creation_token()
        results = self.race([
            lambda: save_assignment(membership=self.manager, cleaned_data=self.data, request_token=token).pk,
            lambda: save_assignment(membership=self.manager, cleaned_data=self.data, request_token=token).pk,
        ])
        self.assertEqual(results[0], results[1])
        self.assertEqual(A.objects.unscoped().count(), 1)
