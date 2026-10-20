from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('inventory', '0012_cart_shipping_address')]

    operations = [
        migrations.CreateModel(
            name='PurchaseImportSubmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField()),
                ('fingerprint', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('organization', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='users.organization')),
                ('tenant', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='users.organization')),
                ('purchase', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='import_submissions', to='inventory.purchase')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('tenant', 'token'), name='uniq_purchase_import_tenant_token')]},
        ),
    ]
