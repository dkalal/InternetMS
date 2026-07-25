from django.db import migrations, models


def forward_invoice_statuses(apps, schema_editor):
    BillingDocument = apps.get_model("billing", "BillingDocument")

    invoice_type = "invoice"
    cancelled = "cancelled"
    reissued = "reissued"
    superseded = "superseded"
    void = "void"

    superseded_ids = list(
        BillingDocument.objects.filter(
            document_type=invoice_type,
            status=cancelled,
            reissued_versions__isnull=False,
        )
        .values_list("id", flat=True)
        .distinct()
    )
    if superseded_ids:
        BillingDocument.objects.filter(id__in=superseded_ids).update(status=superseded)

    BillingDocument.objects.filter(document_type=invoice_type, status=reissued).update(status=superseded)

    void_ids = list(
        BillingDocument.objects.filter(document_type=invoice_type, status=cancelled)
        .exclude(id__in=superseded_ids)
        .exclude(receipts__isnull=False)
        .values_list("id", flat=True)
        .distinct()
    )
    if void_ids:
        BillingDocument.objects.filter(id__in=void_ids).update(status=void)


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0013_billingsheet_billingitem"),
    ]

    operations = [
        migrations.AlterField(
            model_name="billingdocument",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("sent", "Sent"),
                    ("issued", "Issued"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("partially_paid", "Partially Paid"),
                    ("paid", "Paid"),
                    ("void", "Void"),
                    ("superseded", "Superseded"),
                    ("cancelled", "Cancelled"),
                    ("reissued", "Reissued"),
                ],
                db_index=True,
                default="draft",
                max_length=20,
            ),
        ),
        migrations.RunPython(forward_invoice_statuses, reverse_noop),
    ]
