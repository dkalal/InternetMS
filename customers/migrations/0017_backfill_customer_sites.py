from django.db import migrations


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Customer = apps.get_model("customers", "Customer")
    CustomerSite = apps.get_model("customers", "CustomerSite")

    customers = Customer._base_manager.using(db_alias).all().iterator()
    for customer in customers:
        sites = CustomerSite._base_manager.using(db_alias).filter(customer_id=customer.id)
        if sites.exists():
            primary = sites.filter(is_primary=True).first() or sites.first()
            if primary is not None and not sites.filter(is_primary=True).exists():
                sites.exclude(pk=primary.pk).update(is_primary=False)
                primary.is_primary = True
                primary.save(update_fields=["is_primary"])
            continue

        site = CustomerSite._base_manager.using(db_alias).create(
            organization_id=customer.organization_id,
            tenant_id=customer.tenant_id or customer.organization_id,
            customer_id=customer.id,
            name="Main Office",
            location=customer.location,
            address=customer.address,
            ip_address=customer.ip_address,
            vlan_id=customer.vlan_id,
            is_primary=True,
            is_active=True,
        )
        site.packages.set(customer.packages.all())


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0016_customersite"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
