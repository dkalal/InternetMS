from django.db import migrations, models


CHOICES = [
    ("standard", "Standard Customer"),
    ("technician", "Technician Customer"),
    ("wholesale", "Wholesale Customer"),
    ("retail", "Legacy Retail"),
]


class Migration(migrations.Migration):
    dependencies = [("billing", "0022_remove_billingdocument_uniq_payment_reference_per_org_and_more")]

    operations = [
        migrations.AddField(
            model_name="billingdocument",
            name="sale_pricing_category",
            field=models.CharField(
                choices=CHOICES,
                db_index=True,
                default="retail",
                help_text="Select the customer pricing category intentionally for this transaction.",
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="billingdocument",
            name="sale_pricing_category",
            field=models.CharField(
                choices=CHOICES,
                db_index=True,
                default="standard",
                help_text="Select the customer pricing category intentionally for this transaction.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="billinglineitem",
            name="pricing_mode",
            field=models.CharField(
                choices=[
                    ("standard", "Standard"),
                    ("technician", "Technician"),
                    ("retail", "Retail"),
                    ("wholesale", "Wholesale"),
                    ("promotion", "Promotion"),
                    ("manual", "Manual"),
                ],
                default="retail",
                max_length=20,
            ),
        ),
    ]
