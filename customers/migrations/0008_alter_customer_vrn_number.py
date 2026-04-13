from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0007_customer_org_fk"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customer",
            name="vrn_number",
            field=models.CharField(
                blank=True,
                help_text="Optional. Used on invoices/receipts for VAT-registered customers.",
                max_length=50,
                null=True,
                verbose_name="VAT Reg. No. (VRN)",
            ),
        ),
    ]

