from django.db import migrations


def assign_recognizable_icons(apps, schema_editor):
    ProductCategory = apps.get_model('products', 'ProductCategory')
    icon_terms = (
        ('camera', ('camera', 'cctv', 'security')),
        ('tools', ('install', 'tool', 'service')),
        ('laptop', ('laptop', 'computer', 'comput')),
        ('router', ('router', 'wireless', 'wifi')),
        ('switch', ('switch',)),
        ('cable', ('cable', 'wire', 'utp', 'fiber')),
    )
    for category in ProductCategory.objects.filter(icon='layers').iterator():
        name = (category.name or '').casefold()
        category.icon = next(
            (icon for icon, terms in icon_terms if any(term in name for term in terms)),
            'layers',
        )
        category.save(update_fields=['icon'])


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0010_productcategory_icon_productcategory_measure_unit'),
    ]

    operations = [
        migrations.RunPython(assign_recognizable_icons, migrations.RunPython.noop),
    ]
