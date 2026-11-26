from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0033_walk_in_shipping_address"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingdocument",
            name="cash_received",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                editable=False,
                help_text="Cash tendered for a receipt; null for non-cash and legacy records.",
                max_digits=12,
                null=True,
            ),
        ),
    ]
