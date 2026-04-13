from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def map_customer_statuses(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    Customer.objects.filter(status="using").update(status="active")
    Customer.objects.filter(status="pending").update(status="inactive")
    Customer.objects.filter(status="blocked").update(status="suspended")


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0009_rename_customers_cu_organiz_652d7c_idx_customers_c_organiz_cb0907_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="is_deleted",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="customer",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="customer",
            name="deleted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="deleted_customers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(map_customer_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customer",
            name="status",
            field=models.CharField(
                choices=[("active", "Active"), ("inactive", "Inactive"), ("suspended", "Suspended")],
                db_index=True,
                default="active",
                max_length=20,
            ),
        ),
    ]
