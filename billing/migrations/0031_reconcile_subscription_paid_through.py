from django.db import migrations
from django.db.models import Max


def reconcile_paid_through(apps, schema_editor):
    """Advance stale summaries from authoritative paid billing periods."""
    Subscription = apps.get_model("billing", "CustomerSubscription")
    Period = apps.get_model("billing", "SubscriptionPeriod")
    db_alias = schema_editor.connection.alias

    paid_ends = (
        Period._base_manager.using(db_alias)
        .filter(status="paid")
        .values("subscription_id")
        .annotate(paid_end=Max("period_end"))
    )
    for row in paid_ends.iterator():
        subscription = (
            Subscription._base_manager.using(db_alias)
            .filter(pk=row["subscription_id"])
            .only("pk", "paid_through_date")
            .first()
        )
        if subscription is not None and (
            subscription.paid_through_date is None
            or subscription.paid_through_date < row["paid_end"]
        ):
            Subscription._base_manager.using(db_alias).filter(pk=subscription.pk).update(
                paid_through_date=row["paid_end"]
            )


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0030_scope_legacy_subscription_constraint"),
    ]

    operations = [
        migrations.RunPython(reconcile_paid_through, migrations.RunPython.noop),
    ]
