from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("inventory", "0006_cart_sale_pricing_category")]

    operations = [
        migrations.CreateModel(
            name="PurchaseReferenceSequence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("last_number", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="users.organization")),
                ("tenant", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="users.organization")),
            ],
        ),
        migrations.AddConstraint(
            model_name="purchasereferencesequence",
            constraint=models.UniqueConstraint(fields=("tenant",), name="uniq_purchase_reference_sequence_tenant"),
        ),
    ]
