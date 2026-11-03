from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0007_purchasereferencesequence")]

    operations = [
        migrations.AlterField(
            model_name="cart",
            name="sale_pricing_category",
            field=models.CharField(
                choices=[
                    ("customer_tier", "Customer category (automatic)"),
                    ("standard", "Standard"),
                    ("technician", "Technician"),
                    ("wholesale", "Wholesale"),
                    ("retail", "Legacy Retail"),
                ],
                db_index=True,
                default="customer_tier",
                max_length=20,
            ),
        ),
    ]
