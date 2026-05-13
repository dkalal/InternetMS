from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("services", "0006_package_tenant"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="package",
            index=models.Index(fields=["organization", "package_type", "is_active"], name="services_org_type_active_idx"),
        ),
        migrations.AddIndex(
            model_name="package",
            index=models.Index(fields=["tenant", "package_type", "is_active"], name="services_ten_type_active_idx"),
        ),
        migrations.AddIndex(
            model_name="package",
            index=models.Index(fields=["organization", "name"], name="services_org_name_idx"),
        ),
        migrations.AddIndex(
            model_name="package",
            index=models.Index(fields=["organization", "monthly_fee"], name="services_org_monthly_idx"),
        ),
    ]
