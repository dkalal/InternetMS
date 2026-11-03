from django.contrib import admin
from audit.models import AuditLog
from .admin_scoping import TenantScopedAdmin

from .models import (
    Membership, Organization, OrganizationBranding, SupportAccessSession,
    TenantMembership, TenantPermissionGrant,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("is_active",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "organization")
    search_fields = ("organization__name", "user__username", "user__email")


@admin.register(OrganizationBranding)
class OrganizationBrandingAdmin(TenantScopedAdmin):
    list_display = ("organization", "legal_name", "email", "phone")
    search_fields = ("organization__name", "legal_name", "email", "phone")


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ("tenant", "user", "base_role", "is_active", "created_at")
    list_filter = ("base_role", "is_active", "tenant")
    search_fields = ("tenant__name", "user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            previous = TenantMembership.objects.get(pk=obj.pk)
            before = {"base_role": previous.base_role, "is_active": previous.is_active, "tenant_id": previous.tenant_id}
        super().save_model(request, obj, form, change)
        AuditLog.objects.create(
            organization=obj.tenant, tenant=obj.tenant, actor=request.user,
            action="security.member.admin_changed", object_type="TenantMembership", object_id=str(obj.pk),
            old_value=before,
            new_value={"base_role": obj.base_role, "is_active": obj.is_active, "tenant_id": obj.tenant_id},
            metadata={"path": request.path, "method": request.method},
        )


@admin.register(TenantPermissionGrant)
class TenantPermissionGrantAdmin(admin.ModelAdmin):
    list_display = ("membership", "action_code", "scope", "granted_by", "created_at")
    list_filter = ("scope", "action_code")
    readonly_fields = ("created_at",)


@admin.register(SupportAccessSession)
class SupportAccessSessionAdmin(admin.ModelAdmin):
    list_display = ("actor", "tenant", "reason", "started_at", "ended_at")
    readonly_fields = ("actor", "tenant", "reason", "session_key", "started_at", "last_used_at", "ended_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
