import uuid

from django.db import migrations, models


def backfill_customer_uuids(apps, schema_editor):
    Customer = apps.get_model('customers', 'Customer')
    for customer in Customer.objects.filter(uuid__isnull=True).iterator():
        customer.uuid = uuid.uuid4()
        customer.save(update_fields=['uuid'])


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0014_customer_large_list_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='uuid',
            field=models.UUIDField(db_index=True, editable=False, null=True),
        ),
        migrations.RunPython(backfill_customer_uuids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='customer',
            name='uuid',
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
