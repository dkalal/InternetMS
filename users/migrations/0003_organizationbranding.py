from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0002_default_org_and_memberships"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationBranding",
            fields=[
                (
                    "organization",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="branding",
                        serialize=False,
                        to="users.organization",
                    ),
                ),
                ("legal_name", models.CharField(blank=True, default="", max_length=200)),
                ("address_line1", models.CharField(blank=True, default="", max_length=200)),
                ("address_line2", models.CharField(blank=True, default="", max_length=200)),
                ("phone", models.CharField(blank=True, default="", max_length=100)),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("tin_number", models.CharField(blank=True, default="", max_length=50, verbose_name="TIN Number")),
                ("vrn_number", models.CharField(blank=True, default="", max_length=50, verbose_name="VAT Reg. No. (VRN)")),
                ("bank_details", models.TextField(blank=True, default="")),
                ("footer_note", models.TextField(blank=True, default="")),
                ("logo", models.ImageField(blank=True, null=True, upload_to="org_logos/")),
            ],
        ),
    ]

