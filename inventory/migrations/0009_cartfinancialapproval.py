import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0008_alter_cart_sale_pricing_category"),
        ("users", "0011_cart_financial_control_limits"),
    ]

    operations = [
        migrations.CreateModel(
            name="CartFinancialApproval",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("proposed_pricing_category", models.CharField(choices=[("customer_tier", "Customer category (automatic)"), ("standard", "Standard"), ("technician", "Technician"), ("wholesale", "Wholesale"), ("retail", "Legacy Retail")], max_length=20)),
                ("proposed_discount_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("proposed_tax_rate", models.DecimalField(decimal_places=2, max_digits=5)),
                ("reason", models.CharField(max_length=500)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], db_index=True, default="pending", max_length=20)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_note", models.CharField(blank=True, max_length=500)),
                ("cart", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="financial_approvals", to="inventory.cart")),
                ("organization", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="users.organization")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cart_approval_requests", to="users.tenantmembership")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cart_approval_reviews", to="users.tenantmembership")),
                ("tenant", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="users.organization")),
            ],
            options={"ordering": ["-requested_at"]},
        ),
        migrations.AddConstraint(
            model_name="cartfinancialapproval",
            constraint=models.UniqueConstraint(condition=models.Q(("status", "pending")), fields=("cart",), name="one_pending_financial_approval_per_cart"),
        ),
    ]
