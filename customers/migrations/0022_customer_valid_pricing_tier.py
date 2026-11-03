from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0021_retire_corporate_vip_pricing_tiers"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="customer",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    pricing_tier__in=["retail", "technician", "wholesale"]
                ),
                name="customer_valid_pricing_tier",
            ),
        ),
    ]
