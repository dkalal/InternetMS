from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from users.models import Organization, TenantMembership

from .models import TechnicianWorkReport


class WorkReportServiceDayMigrationTests(TransactionTestCase):
    migrate_from = ("work_reports", "0004_workreportserviceday")
    migrate_to = ("work_reports", "0005_backfill_workreportservicedays")

    def setUp(self):
        tenant = Organization.objects.create(name="Migration tenant", slug="migration-tenant")
        user = get_user_model().objects.create_user(username="migration-technician")
        technician = TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            base_role=TenantMembership.BaseRole.TECHNICIAN,
        )
        self.report = TechnicianWorkReport.objects.create(
            tenant=tenant,
            technician=technician,
            work_title="Existing single-day report",
            service_date=date(2026, 9, 2),
            activity_description="Existing work",
            agreed_amount=Decimal("100.00"),
        )
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.old_apps = executor.loader.project_state([self.migrate_from]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_backfill_creates_one_tenant_matched_day_per_existing_report(self):
        OldServiceDay = self.old_apps.get_model("work_reports", "WorkReportServiceDay")
        self.assertEqual(OldServiceDay.objects.count(), 0)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        ServiceDay = apps.get_model("work_reports", "WorkReportServiceDay")

        rows = list(ServiceDay.objects.values("report_id", "tenant_id", "service_date"))
        self.assertEqual(rows, [{
            "report_id": self.report.pk,
            "tenant_id": self.report.tenant_id,
            "service_date": date(2026, 9, 2),
        }])


class AssignmentMigrationTests(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_report_survives_assignment_schema_and_reversal(self):
        before = ("work_reports", "0006_alter_workreporthistory_event_technicianpaymentbatch_and_more")
        after = ("work_reports", "0007_workassignment_technicianworkreport_assignment_and_more")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        tenant = apps.get_model("users", "Organization").objects.create(name="Synthetic legacy tenant", slug="legacy-assignment-migration")
        user = apps.get_model("auth", "User").objects.create(username="synthetic-legacy-technician")
        member = apps.get_model("users", "TenantMembership").objects.create(tenant=tenant, user=user, base_role="TECHNICIAN")
        old_report = apps.get_model("work_reports", "TechnicianWorkReport").objects.create(
            tenant=tenant, technician=member, work_title="Historical report retained",
            service_date=date(2026, 8, 1), activity_description="Historical completed work",
            agreed_amount=Decimal("100000.00"),
        )
        executor = MigrationExecutor(connection)
        executor.migrate([after])
        apps = executor.loader.project_state([after]).apps
        report = apps.get_model("work_reports", "TechnicianWorkReport").objects.get(pk=old_report.pk)
        self.assertIsNone(report.assignment_id)
        self.assertEqual(report.agreed_amount, Decimal("100000.00"))
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        self.assertTrue(apps.get_model("work_reports", "TechnicianWorkReport").objects.filter(pk=old_report.pk).exists())
