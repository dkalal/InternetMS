from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [('customers', '0022_customer_valid_pricing_tier')]

    operations = [
        migrations.AddField(
            model_name='customer', name='is_pos_placeholder',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(
                fields=('tenant',), condition=Q(is_pos_placeholder=True),
                name='uniq_pos_placeholder_per_tenant',
            ),
        ),
    ]
