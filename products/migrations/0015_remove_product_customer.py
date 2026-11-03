from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('products', '0014_unitofmeasure_product_sales_unit_and_more')]
    # Expand/contract rollout: remove the relation from Django immediately while
    # retaining the nullable legacy column for rollback and a later retention cleanup.
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.RemoveField(model_name='product', name='customer')],
            database_operations=[],
        ),
    ]
