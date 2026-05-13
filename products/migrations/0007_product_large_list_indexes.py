from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0006_product_allow_wholesale_product_retail_price_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["organization", "category", "is_active"], name="products_org_cat_active_idx"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["tenant", "category", "is_active"], name="products_ten_cat_active_idx"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["organization", "name"], name="products_org_name_idx"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["organization", "quantity"], name="products_org_quantity_idx"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["organization", "retail_price"], name="products_org_retail_idx"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["organization", "wholesale_price"], name="products_org_wholesale_idx"),
        ),
    ]
