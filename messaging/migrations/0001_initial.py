from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("customers", "0014_customer_large_list_indexes"),
        ("users", "0006_backfill_tenant_and_access_profiles"),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(db_index=True, max_length=120)),
                ("category", models.CharField(choices=[("invoice", "Invoice"), ("quotation", "Quotation"), ("reminder", "Reminder"), ("support", "Support"), ("general", "General"), ("receipt", "Receipt")], db_index=True, max_length=20)),
                ("content", models.TextField()),
                ("variables_schema", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant", models.ForeignKey(blank=True, db_index=True, help_text="Leave blank for a global reusable template.", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="message_templates", to="users.organization")),
            ],
            options={
                "ordering": ["tenant_id", "category", "name"],
            },
        ),
        migrations.CreateModel(
            name="WhatsAppManualMessageLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone_number", models.CharField(max_length=20)),
                ("message_content", models.TextField()),
                ("related_object_type", models.CharField(blank=True, db_index=True, default="", max_length=40)),
                ("related_object_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("status", models.CharField(choices=[("opened", "Opened"), ("sent_manual", "Sent manually"), ("failed", "Failed")], db_index=True, default="opened", max_length=20)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="whatsapp_manual_message_logs", to="customers.customer")),
                ("sent_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="whatsapp_manual_message_logs", to=settings.AUTH_USER_MODEL)),
                ("template_used", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="manual_message_logs", to="messaging.messagetemplate")),
                ("tenant", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="whatsapp_manual_message_logs", to="users.organization")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="messagetemplate",
            index=models.Index(fields=["tenant", "category", "is_active"], name="messaging_m_tenant__e30162_idx"),
        ),
        migrations.AddIndex(
            model_name="messagetemplate",
            index=models.Index(fields=["category", "is_active"], name="messaging_m_categor_23fedd_idx"),
        ),
        migrations.AddConstraint(
            model_name="messagetemplate",
            constraint=models.UniqueConstraint(condition=models.Q(("is_active", True)), fields=("tenant", "name"), name="uniq_active_template_name_per_tenant"),
        ),
        migrations.AddConstraint(
            model_name="messagetemplate",
            constraint=models.UniqueConstraint(condition=models.Q(("is_active", True), ("tenant__isnull", True)), fields=("name",), name="uniq_active_global_template_name"),
        ),
        migrations.AddIndex(
            model_name="whatsappmanualmessagelog",
            index=models.Index(fields=["tenant", "created_at"], name="messaging_w_tenant__51cea5_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappmanualmessagelog",
            index=models.Index(fields=["tenant", "customer", "created_at"], name="messaging_w_tenant__b750b5_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappmanualmessagelog",
            index=models.Index(fields=["tenant", "related_object_type", "related_object_id"], name="wa_log_tenant_related_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappmanualmessagelog",
            index=models.Index(fields=["tenant", "status", "created_at"], name="messaging_w_tenant__6ad471_idx"),
        ),
    ]
