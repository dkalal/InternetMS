from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('integrations', '0004_externalassetreference')]

    operations = [
        migrations.AddField(
            model_name='externalassetreference',
            name='display_name',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='externalassetreference',
            name='custom_attributes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='externalassetreference',
            name='source_url',
            field=models.URLField(blank=True),
        ),
    ]
