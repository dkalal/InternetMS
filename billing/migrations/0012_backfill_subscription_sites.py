from django.db import migrations


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    CustomerSubscription = apps.get_model("billing", "CustomerSubscription")
    CustomerSite = apps.get_model("customers", "CustomerSite")

    subscriptions = CustomerSubscription._base_manager.using(db_alias).select_related("customer").filter(site__isnull=True)
    for subscription in subscriptions.iterator():
        site = (
            CustomerSite._base_manager.using(db_alias)
            .filter(customer_id=subscription.customer_id, is_primary=True)
            .first()
            or CustomerSite._base_manager.using(db_alias).filter(customer_id=subscription.customer_id).first()
        )
        if site is None:
            continue
        CustomerSubscription._base_manager.using(db_alias).filter(pk=subscription.pk).update(site_id=site.id)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0011_customersubscription_site_and_more"),
        ("customers", "0017_backfill_customer_sites"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
