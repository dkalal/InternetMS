from django.db import migrations, models


ANNUAL_DOCUMENT_TYPES = ("quotation", "invoice", "receipt")


def discard_legacy_daily_counters(apps, schema_editor):
    """Start the new format at 0001 without changing any stored document."""
    DocumentSequence = apps.get_model("billing", "DocumentSequence")
    DocumentSequence.objects.filter(document_type__in=ANNUAL_DOCUMENT_TYPES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0023_sale_pricing_category"),
    ]

    operations = [
        migrations.RunPython(discard_legacy_daily_counters, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="documentsequence",
            name="uniq_billing_sequence_per_tenant_day",
        ),
        migrations.AlterField(
            model_name="documentsequence",
            name="sequence_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentsequence",
            name="year",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="documentsequence",
            constraint=models.UniqueConstraint(
                fields=("tenant", "document_type", "year"),
                name="uniq_billing_sequence_per_tenant_year",
            ),
        ),
        migrations.AddConstraint(
            model_name="documentsequence",
            constraint=models.UniqueConstraint(
                condition=models.Q(document_type="credit_note"),
                fields=("tenant", "document_type", "sequence_date"),
                name="uniq_credit_sequence_per_tenant_day",
            ),
        ),
        migrations.AddIndex(
            model_name="documentsequence",
            index=models.Index(
                fields=["tenant", "document_type", "year"],
                name="bill_seq_tenant_type_year_idx",
            ),
        ),
    ]
