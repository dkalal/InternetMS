from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('billing', '0032_walk_in_identity_snapshot'),
        ('inventory', '0012_cart_shipping_address'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingdocument',
            name='shipping_address_snapshot',
            field=models.CharField(blank=True, default='', editable=False, max_length=500),
        ),
    ]
