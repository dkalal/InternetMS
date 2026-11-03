from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0025_billinglineitem_unit_snapshot")]

    operations = [
        migrations.AlterField(
            model_name="billingdocument",
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
                help_text="Select the customer pricing category intentionally for this transaction.",
                max_length=20,
            ),
        ),
    ]
