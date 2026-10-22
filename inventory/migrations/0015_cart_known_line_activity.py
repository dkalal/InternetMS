from django.db import migrations


def preserve_known_line_activity(apps, schema_editor):
    from django.db.models import Max, F
    Cart = apps.get_model('inventory', 'Cart')
    # Use recorded timestamps, never the migration time. Lifecycle dates stay NULL.
    rows = Cart.objects.using(schema_editor.connection.alias).filter(status='draft').annotate(
        line_activity=Max('lines__updated_at'),
    ).filter(line_activity__gt=F('updated_at')).values_list('pk', 'line_activity')
    for pk, activity in rows.iterator(chunk_size=500):
        Cart.objects.using(schema_editor.connection.alias).filter(pk=pk, updated_at__lt=activity).update(updated_at=activity)


class Migration(migrations.Migration):
    dependencies = [('inventory', '0014_cart_lifecycle')]
    operations = [migrations.RunPython(preserve_known_line_activity, migrations.RunPython.noop)]
