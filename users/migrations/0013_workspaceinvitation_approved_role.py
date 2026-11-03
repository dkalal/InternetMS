from django.db import migrations, models


def backfill_roles(apps, schema_editor):
    Invitation = apps.get_model("users", "WorkspaceInvitation")
    for invitation in Invitation.objects.select_related("membership").iterator():
        invitation.approved_role = invitation.membership.base_role
        invitation.save(update_fields=["approved_role"])


class Migration(migrations.Migration):
    dependencies = [("users", "0012_workspaceinvitation")]

    operations = [
        migrations.AddField(
            model_name="workspaceinvitation", name="approved_role",
            field=models.CharField(max_length=20, default=""), preserve_default=False,
        ),
        migrations.RunPython(backfill_roles, migrations.RunPython.noop),
    ]
