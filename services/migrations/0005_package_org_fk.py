from django.db import migrations, models
import django.db.models.deletion


def backfill_org(apps, schema_editor):
    Organization = apps.get_model('users', 'Organization')
    Package = apps.get_model('services', 'Package')

    org = Organization.objects.filter(slug='default').first()
    if not org:
        org = Organization.objects.create(name='Default Organization', slug='default', is_active=True)

    Package.objects.filter(organization__isnull=True).update(organization=org)


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0002_default_org_and_memberships'),
        ('services', '0004_remove_package_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='package',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='packages',
                to='users.organization',
            ),
        ),
        migrations.RunPython(backfill_org, migrations.RunPython.noop),
    ]

