from django.db import migrations, models
import django.db.models.deletion


def backfill_org(apps, schema_editor):
    Organization = apps.get_model('users', 'Organization')
    Customer = apps.get_model('customers', 'Customer')
    CustomerDocument = apps.get_model('customers', 'CustomerDocument')

    org = Organization.objects.filter(slug='default').first()
    if not org:
        org = Organization.objects.create(name='Default Organization', slug='default', is_active=True)

    Customer.objects.filter(organization__isnull=True).update(organization=org)
    CustomerDocument.objects.filter(organization__isnull=True).update(organization=org)


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0002_default_org_and_memberships'),
        ('customers', '0006_alter_customer_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='customers',
                to='users.organization',
            ),
        ),
        migrations.AddField(
            model_name='customerdocument',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='customer_documents',
                to='users.organization',
            ),
        ),
        migrations.RunPython(backfill_org, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['organization', 'customer_type'], name='customers_cu_organiz_652d7c_idx'),
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['organization', 'status'], name='customers_cu_organiz_3d07b4_idx'),
        ),
        migrations.AddIndex(
            model_name='customerdocument',
            index=models.Index(fields=['organization', 'document_type'], name='customers_cu_organiz_6dc5d3_idx'),
        ),
    ]

