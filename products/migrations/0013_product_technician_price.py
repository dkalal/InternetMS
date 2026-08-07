from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0012_alter_product_tenant")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="technician_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["organization", "technician_price"], name="products_org_tech_idx"),
        ),
    ]
