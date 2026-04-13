from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("customers", "0007_customer_org_fk"),
        ("products", "0004_product_org_fk"),
        ("services", "0005_package_org_fk"),
        ("users", "0003_organizationbranding"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_type", models.CharField(choices=[("quotation", "Quotation"), ("invoice", "Invoice"), ("receipt", "Receipt")], db_index=True, max_length=20)),
                ("number", models.CharField(max_length=60)),
                ("issue_date", models.DateField(db_index=True)),
                ("due_date", models.DateField(blank=True, db_index=True, null=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("sent", "Sent"), ("approved", "Approved"), ("rejected", "Rejected"), ("paid", "Paid"), ("cancelled", "Cancelled")], db_index=True, default="draft", max_length=20)),
                ("currency", models.CharField(default="TZS", max_length=10)),
                ("subtotal", models.DecimalField(decimal_places=2, default="0.00", max_digits=12)),
                ("tax_rate", models.DecimalField(decimal_places=2, default="18.00", max_digits=5)),
                ("tax_amount", models.DecimalField(decimal_places=2, default="0.00", max_digits=12)),
                ("total", models.DecimalField(decimal_places=2, default="0.00", max_digits=12)),
                ("notes", models.TextField(blank=True, default="")),
                ("payment_date", models.DateField(blank=True, null=True)),
                ("payment_method", models.CharField(blank=True, default="", max_length=50)),
                ("payment_reference", models.CharField(blank=True, default="", help_text="Optional idempotency key (e.g., bank slip id, mobile money txn id).", max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_billing_documents", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="billing_documents", to="customers.customer")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="billing_documents", to="users.organization")),
                ("invoice", models.ForeignKey(blank=True, limit_choices_to={"document_type": "invoice"}, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="receipts", to="billing.billingdocument")),
            ],
            options={"ordering": ["-issue_date", "-created_at"]},
        ),
        migrations.CreateModel(
            name="DocumentSequence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_type", models.CharField(choices=[("quotation", "Quotation"), ("invoice", "Invoice"), ("receipt", "Receipt")], max_length=20)),
                ("year", models.PositiveIntegerField()),
                ("month", models.PositiveIntegerField()),
                ("next_number", models.PositiveIntegerField(default=1)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="billing_sequences", to="users.organization")),
            ],
        ),
        migrations.CreateModel(
            name="BillingLineItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("description", models.TextField(blank=True, default="")),
                ("quantity", models.DecimalField(decimal_places=2, default="1.00", max_digits=10)),
                ("unit_price", models.DecimalField(decimal_places=2, default="0.00", max_digits=12)),
                ("line_total", models.DecimalField(decimal_places=2, default="0.00", max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="billing.billingdocument")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="billing_line_items", to="users.organization")),
                ("package", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="services.package")),
                ("product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="products.product")),
            ],
        ),
        migrations.AddConstraint(
            model_name="billingdocument",
            constraint=models.UniqueConstraint(fields=("organization", "document_type", "number"), name="uniq_billing_number_per_org"),
        ),
        migrations.AddConstraint(
            model_name="billingdocument",
            constraint=models.UniqueConstraint(condition=models.Q(("payment_reference__exact", ""), _negated=True), fields=("organization", "payment_reference"), name="uniq_payment_reference_per_org"),
        ),
        migrations.AddConstraint(
            model_name="documentsequence",
            constraint=models.UniqueConstraint(fields=("organization", "document_type", "year", "month"), name="uniq_billing_sequence_per_org_month"),
        ),
        migrations.AddIndex(
            model_name="billingdocument",
            index=models.Index(fields=["organization", "document_type", "issue_date"], name="billing_doc_org_type_date_idx"),
        ),
        migrations.AddIndex(
            model_name="billingdocument",
            index=models.Index(fields=["organization", "document_type", "status"], name="billing_doc_org_type_status_idx"),
        ),
        migrations.AddIndex(
            model_name="billinglineitem",
            index=models.Index(fields=["organization", "created_at"], name="billing_item_org_created_idx"),
        ),
        migrations.AddIndex(
            model_name="billinglineitem",
            index=models.Index(fields=["organization", "document"], name="billing_item_org_doc_idx"),
        ),
    ]

