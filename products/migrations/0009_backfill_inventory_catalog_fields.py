from django.db import migrations


def forward(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    for product in Product.objects.filter(sku='').iterator():
        tenant_id = product.tenant_id or product.organization_id or 0
        product.sku = f'LEGACY-{tenant_id}-{product.pk}'
        product.save(update_fields=['sku'])


def reverse(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    Product.objects.filter(sku__startswith='LEGACY-').update(sku='')


class Migration(migrations.Migration):
    dependencies = [('products', '0008_product_brand_product_is_serialized_and_more')]
    operations = [migrations.RunPython(forward, reverse)]
