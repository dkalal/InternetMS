from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0013_customer_pricing_tier"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["organization", "phone"], name="customers_org_phone_idx"),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["organization", "ip_address"], name="customers_org_ip_idx"),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["organization", "vlan_id"], name="customers_org_vlan_idx"),
        ),
    ]
