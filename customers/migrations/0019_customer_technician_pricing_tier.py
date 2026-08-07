from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("customers", "0018_alter_customer_tenant_alter_customerdocument_tenant_and_more")]

    operations = [
        migrations.AlterField(
            model_name="customer",
            name="pricing_tier",
            field=models.CharField(
                choices=[
                    ("retail", "Standard"),
                    ("technician", "Technician"),
                    ("wholesale", "Wholesale"),
                    ("corporate", "Corporate"),
                    ("vip", "VIP"),
                ],
                db_index=True,
                default="retail",
                max_length=20,
            ),
        ),
    ]
