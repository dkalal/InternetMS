from decimal import Decimal

from django.db import migrations


def forward(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    InventoryBalance = apps.get_model('inventory', 'InventoryBalance')
    StockMovement = apps.get_model('inventory', 'StockMovement')
    for product in Product.objects.filter(item_type='physical', track_stock=True).iterator():
        tenant_id = product.tenant_id or product.organization_id
        if tenant_id is None:
            continue
        quantity_value = product.quantity or Decimal('0.00')
        stock_value = Decimal(product.stock or 0)
        quantity = max(quantity_value, stock_value, Decimal('0.00'))
        balance, created = InventoryBalance.objects.get_or_create(
            product_id=product.pk,
            defaults={
                'organization_id': tenant_id,
                'tenant_id': tenant_id,
                'quantity': quantity,
                'average_cost': product.buying_price or Decimal('0.00'),
            },
        )
        if created and quantity > 0:
            StockMovement.objects.create(
                organization_id=tenant_id,
                tenant_id=tenant_id,
                product_id=product.pk,
                movement_type='opening',
                quantity=quantity,
                balance_after=quantity,
                unit_cost=product.buying_price or Decimal('0.00'),
            )


def reverse(apps, schema_editor):
    StockMovement = apps.get_model('inventory', 'StockMovement')
    InventoryBalance = apps.get_model('inventory', 'InventoryBalance')
    product_ids = list(StockMovement.objects.filter(movement_type='opening', purchase_line__isnull=True, adjustment__isnull=True, billing_line__isnull=True).values_list('product_id', flat=True))
    StockMovement.objects.filter(product_id__in=product_ids, movement_type='opening').delete()
    InventoryBalance.objects.filter(product_id__in=product_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0001_initial'),
        ('products', '0009_backfill_inventory_catalog_fields'),
    ]
    operations = [migrations.RunPython(forward, reverse)]
