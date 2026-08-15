from django.db import migrations, models


def consolidate_legacy_pricing_tiers(apps, schema_editor):
    """Preserve effective pricing while retiring duplicate tier labels."""
    Customer = apps.get_model("customers", "Customer")
    Customer.objects.filter(pricing_tier__in=("corporate", "vip")).update(
        pricing_tier="wholesale"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0020_internetservice"),
    ]

    operations = [
        migrations.RunPython(
            consolidate_legacy_pricing_tiers,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="customer",
            name="pricing_tier",
            field=models.CharField(
                choices=[
                    ("retail", "Standard"),
                    ("technician", "Technician"),
                    ("wholesale", "Wholesale"),
                ],
                db_index=True,
                default="retail",
                max_length=20,
            ),
        ),
    ]
