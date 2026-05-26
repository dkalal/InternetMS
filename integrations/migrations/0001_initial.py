from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('users', '0006_backfill_tenant_and_access_profiles'),
    ]

    operations = [
        migrations.CreateModel(
            name='IntegrationConsumer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='integration_consumers', to='users.organization')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='integration_consumer', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['organization__name', 'name'],
                'indexes': [models.Index(fields=['organization', 'is_active'], name='integration_organiz_47f1e7_idx')],
            },
        ),
    ]
