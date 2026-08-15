import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('customers', '0022_customer_valid_pricing_tier'),
        ('integrations', '0003_alter_integrationconsumer_tenant'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExternalAssetReference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('external_uuid', models.UUIDField()),
                ('asset_tag', models.CharField(blank=True, max_length=100)),
                ('serial_number', models.CharField(blank=True, max_length=100)),
                ('category_name', models.CharField(max_length=200)),
                ('branch_name', models.CharField(blank=True, max_length=200)),
                ('status', models.CharField(max_length=32)),
                ('description', models.TextField(blank=True)),
                ('source_updated_at', models.DateTimeField()),
                ('last_synced_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='external_assets', to='customers.customer')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='external_asset_references', to='users.organization')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tenant_external_asset_references', to='users.organization')),
            ],
            options={
                'ordering': ['category_name', 'asset_tag', 'external_uuid'],
                'indexes': [models.Index(fields=['tenant', 'customer'], name='extasset_tenant_customer'), models.Index(fields=['tenant', 'status'], name='extasset_tenant_status')],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'external_uuid'), name='uniq_ext_asset_per_tenant')],
            },
        ),
    ]
