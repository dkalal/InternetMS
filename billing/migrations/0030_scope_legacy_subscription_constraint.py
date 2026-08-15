from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0029_backfill_internet_services"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="customersubscription",
            name="uniq_active_subscription_per_tenant_site_package",
        ),
        migrations.AddConstraint(
            model_name="customersubscription",
            constraint=models.UniqueConstraint(
                fields=("tenant", "customer", "site", "package"),
                condition=models.Q(status="active", internet_service__isnull=True),
                name="uniq_active_subscription_per_tenant_site_package",
            ),
        ),
    ]
