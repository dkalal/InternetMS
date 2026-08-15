from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("users", "0010_alter_organizationbranding_tenant")]

    operations = [
        migrations.AddField(
            model_name="tenantpermissiongrant",
            name="allowed_pricing_categories",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="tenantpermissiongrant",
            name="max_discount_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="tenantpermissiongrant",
            name="max_discount_percent",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
    ]
