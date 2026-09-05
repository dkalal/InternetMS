from django.db import migrations


def backfill_service_days(apps, schema_editor):
    WorkReport = apps.get_model("work_reports", "TechnicianWorkReport")
    ServiceDay = apps.get_model("work_reports", "WorkReportServiceDay")
    database = schema_editor.connection.alias
    rows = (
        WorkReport.objects.using(database)
        .order_by("pk")
        .values_list("pk", "tenant_id", "service_date")
    )
    ServiceDay.objects.using(database).bulk_create(
        [
            ServiceDay(
                report_id=report_id,
                tenant_id=tenant_id,
                service_date=service_date,
                activity_note="",
            )
            for report_id, tenant_id, service_date in rows
        ],
        batch_size=500,
    )


def remove_backfilled_service_days(apps, schema_editor):
    ServiceDay = apps.get_model("work_reports", "WorkReportServiceDay")
    ServiceDay.objects.using(schema_editor.connection.alias).all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("work_reports", "0004_workreportserviceday"),
    ]

    operations = [
        migrations.RunPython(
            backfill_service_days,
            reverse_code=remove_backfilled_service_days,
        ),
    ]
