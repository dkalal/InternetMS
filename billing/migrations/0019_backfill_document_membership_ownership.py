from django.db import migrations


def forwards(apps, schema_editor):
    BillingDocument = apps.get_model("billing", "BillingDocument")
    TenantMembership = apps.get_model("users", "TenantMembership")
    memberships = {
        (item.user_id, item.tenant_id): item.id
        for item in TenantMembership.objects.filter(is_active=True).order_by("id")
    }
    for document in BillingDocument.objects.filter(created_by_membership_id__isnull=True).iterator():
        membership_id = memberships.get((document.created_by_id, document.tenant_id))
        if membership_id is None:
            continue
        updates = {
            "created_by_membership_id": membership_id,
            "responsible_membership_id": membership_id,
            "last_modified_by_membership_id": membership_id,
        }
        if document.issued_at is not None:
            updates["issued_by_membership_id"] = membership_id
        if document.document_type == "receipt":
            updates["payment_recorded_by_membership_id"] = membership_id
        BillingDocument.objects.filter(pk=document.pk).update(**updates)


def backwards(apps, schema_editor):
    apps.get_model("billing", "BillingDocument").objects.update(
        created_by_membership_id=None,
        responsible_membership_id=None,
        issued_by_membership_id=None,
        payment_recorded_by_membership_id=None,
        last_modified_by_membership_id=None,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0018_billingdocument_created_by_membership_and_more"),
        ("users", "0008_backfill_tenant_memberships"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
