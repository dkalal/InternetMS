from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0011_purchaseline_authoritative_purchase_total_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cart',
            name='shipping_address',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
