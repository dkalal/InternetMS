from __future__ import annotations

from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0012_backfill_subscription_sites"),
        ("customers", "0017_backfill_customer_sites"),
        ("users", "0006_backfill_tenant_and_access_profiles"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingSheet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "organization",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="billing_sheets",
                        to="users.organization",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        blank=True,
                        db_index=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tenant_billing_sheets",
                        to="users.organization",
                    ),
                ),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="billing_sheets",
                        to="customers.customer",
                    ),
                ),
                (
                    "invoice",
                    models.ForeignKey(
                        blank=True,
                        limit_choices_to={"document_type": "invoice"},
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_billing_sheets",
                        to="billing.billingdocument",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_billing_sheets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("reference_number", models.CharField(db_index=True, max_length=60)),
                ("title", models.CharField(max_length=200)),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("invoiced", "Invoiced")],
                        db_index=True,
                        default="open",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="billingsheet",
            constraint=models.UniqueConstraint(
                fields=["organization", "reference_number"],
                name="uniq_billing_sheet_ref_per_org",
            ),
        ),
        migrations.AddIndex(
            model_name="billingsheet",
            index=models.Index(fields=["organization", "status", "created_at"], name="billing_billingsheet_org_status_idx"),
        ),
        migrations.AddIndex(
            model_name="billingsheet",
            index=models.Index(fields=["tenant", "status", "created_at"], name="billing_billingsheet_ten_status_idx"),
        ),
        migrations.AddIndex(
            model_name="billingsheet",
            index=models.Index(fields=["organization", "customer", "status"], name="billing_billingsheet_org_cust_idx"),
        ),
        migrations.CreateModel(
            name="BillingItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "billing_sheet",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="billing.billingsheet",
                    ),
                ),
                ("description", models.CharField(max_length=300)),
                ("quantity", models.DecimalField(decimal_places=2, default=Decimal("1.00"), max_digits=10)),
                ("unit_price", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("total_price", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]
