from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0008_billinglineitem_base_unit_price_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="billingdocument",
            index=models.Index(fields=["organization", "document_type", "number"], name="billing_doc_org_type_num_idx"),
        ),
        migrations.AddIndex(
            model_name="billingdocument",
            index=models.Index(fields=["organization", "document_type", "customer", "issue_date"], name="billing_doc_org_cust_date_idx"),
        ),
        migrations.AddIndex(
            model_name="billingdocument",
            index=models.Index(fields=["organization", "document_type", "due_date"], name="billing_doc_org_due_idx"),
        ),
        migrations.AddIndex(
            model_name="billingdocument",
            index=models.Index(fields=["organization", "document_type", "total"], name="billing_doc_org_total_idx"),
        ),
        migrations.AddIndex(
            model_name="promotion",
            index=models.Index(fields=["organization", "name"], name="billing_promo_org_name_idx"),
        ),
        migrations.AddIndex(
            model_name="promotion",
            index=models.Index(fields=["organization", "reward_type"], name="billing_promo_org_reward_idx"),
        ),
        migrations.AddIndex(
            model_name="promotion",
            index=models.Index(fields=["organization", "valid_from", "valid_until"], name="billing_promo_org_valid_idx"),
        ),
    ]
