from django.conf import settings
from django.db import migrations


def create_default_org_and_memberships(apps, schema_editor):
    Organization = apps.get_model('users', 'Organization')
    Membership = apps.get_model('users', 'Membership')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))

    org, _ = Organization.objects.get_or_create(
        slug='default',
        defaults={'name': 'Default Organization', 'is_active': True},
    )

    for user in User.objects.all():
        role = 'admin' if getattr(user, 'is_superuser', False) else 'member'
        Membership.objects.get_or_create(
            organization=org,
            user=user,
            defaults={'role': role, 'is_active': True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_org_and_memberships, migrations.RunPython.noop),
    ]

