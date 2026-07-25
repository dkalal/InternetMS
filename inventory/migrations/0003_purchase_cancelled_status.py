from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0002_backfill_legacy_stock')]

    operations = [
        migrations.AlterField(
            model_name='purchase',
            name='status',
            field=models.CharField(
                choices=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled')],
                db_index=True,
                default='draft',
                max_length=20,
            ),
        ),
    ]
