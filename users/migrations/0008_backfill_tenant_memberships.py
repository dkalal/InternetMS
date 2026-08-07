from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    Organization = apps.get_model("users", "Organization")
    Membership = apps.get_model("users", "Membership")
    TenantMembership = apps.get_model("users", "TenantMembership")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))

    default_tenant, _ = Organization.objects.get_or_create(
        slug="default-tenant",
        defaults={"name": "Default Tenant", "is_active": True},
    )
    legacy_by_user = {}
    for legacy in Membership.objects.filter(is_active=True).order_by("organization_id", "id"):
        legacy_by_user.setdefault(legacy.user_id, []).append(legacy)

    for user in User.objects.all().order_by("id"):
        legacy_memberships = legacy_by_user.get(user.id, [])
        if not legacy_memberships:
            role = "SUPER_ADMIN" if user.is_superuser else "SALES"
            TenantMembership.objects.get_or_create(
                tenant_id=default_tenant.id,
                user_id=user.id,
                defaults={"base_role": role, "is_active": user.is_active},
            )
            continue
        for legacy in legacy_memberships:
            if user.is_superuser:
                role = "SUPER_ADMIN"
            elif legacy.role in {"owner", "admin"}:
                role = "ADMIN_MANAGER"
            else:
                role = "SALES"
            TenantMembership.objects.get_or_create(
                tenant_id=legacy.organization_id,
                user_id=user.id,
                defaults={"base_role": role, "is_active": legacy.is_active and user.is_active},
            )


def backwards(apps, schema_editor):
    apps.get_model("users", "TenantPermissionGrant").objects.all().delete()
    apps.get_model("users", "SupportAccessSession").objects.all().delete()
    apps.get_model("users", "TenantMembership").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0007_tenantmembership_tenantpermissiongrant_and_more"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
