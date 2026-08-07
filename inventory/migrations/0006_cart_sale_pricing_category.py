from django.db import migrations, models


CHOICES = [
    ("standard", "Standard Customer"),
    ("technician", "Technician Customer"),
    ("wholesale", "Wholesale Customer"),
    ("retail", "Legacy Retail"),
]


class Migration(migrations.Migration):
    dependencies = [("inventory", "0005_alter_cartline_tenant_and_more")]

    operations = [
        migrations.AddField(
            model_name="cart",
            name="sale_pricing_category",
            field=models.CharField(choices=CHOICES, db_index=True, default="retail", max_length=20),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="cart",
            name="sale_pricing_category",
            field=models.CharField(choices=CHOICES, db_index=True, default="standard", max_length=20),
        ),
    ]
