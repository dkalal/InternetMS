from django.db import migrations, models


def forward_document_lifecycle_updates(apps, schema_editor):
    BillingDocument = apps.get_model("billing", "BillingDocument")

    quotation_type = "quotation"

    BillingDocument.objects.filter(
        document_type=quotation_type,
        status="approved",
    ).update(status="accepted")

    BillingDocument.objects.filter(
        document_type=quotation_type,
        status="issued",
    ).update(status="sent")

    for document in BillingDocument.objects.filter(document_type=quotation_type).iterator():
        fallback_dt = document.updated_at or document.issued_at or document.created_at
        updates = {}
        if document.status == "sent" and document.sent_at is None:
            updates["sent_at"] = fallback_dt
        elif document.status == "accepted" and document.accepted_at is None:
            updates["accepted_at"] = fallback_dt
        elif document.status == "rejected" and document.rejected_at is None:
            updates["rejected_at"] = fallback_dt
        elif document.status == "expired" and document.expired_at is None:
            updates["expired_at"] = fallback_dt
        elif document.status == "converted" and document.converted_at is None:
            updates["converted_at"] = fallback_dt
        if updates:
            BillingDocument.objects.filter(pk=document.pk).update(**updates)


def reverse_document_lifecycle_updates(apps, schema_editor):
    BillingDocument = apps.get_model("billing", "BillingDocument")
    quotation_type = "quotation"

    BillingDocument.objects.filter(document_type=quotation_type, status="accepted").update(status="approved")
    BillingDocument.objects.filter(document_type=quotation_type, status="sent").update(status="issued")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("billing", "0014_invoice_lifecycle_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingdocument",
            name="accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="billingdocument",
            name="converted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="billingdocument",
            name="converted_invoice",
            field=models.ForeignKey(blank=True, limit_choices_to={"document_type": "invoice"}, null=True, on_delete=models.PROTECT, related_name="source_quotation_versions", to="billing.billingdocument"),
        ),
        migrations.AddField(
            model_name="billingdocument",
            name="expired_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="billingdocument",
            name="rejected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="billingdocument",
            name="sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="billingdocument",
            name="source_quotation",
            field=models.ForeignKey(blank=True, limit_choices_to={"document_type": "quotation"}, null=True, on_delete=models.PROTECT, related_name="generated_invoices", to="billing.billingdocument"),
        ),
        migrations.AddField(
            model_name="billingdocument",
            name="superseded_by",
            field=models.ForeignKey(blank=True, limit_choices_to={"document_type": "invoice"}, null=True, on_delete=models.PROTECT, related_name="superseded_invoices", to="billing.billingdocument"),
        ),
        migrations.AddField(
            model_name="billingdocument",
            name="voided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="billingdocument",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("sent", "Sent"),
                    ("issued", "Issued"),
                    ("accepted", "Accepted"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("expired", "Expired"),
                    ("converted", "Converted"),
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
        migrations.RunPython(forward_document_lifecycle_updates, reverse_document_lifecycle_updates),
    ]
