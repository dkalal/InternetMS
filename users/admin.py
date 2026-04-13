from django.contrib import admin

from .models import Membership, Organization, OrganizationBranding


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
class OrganizationBrandingAdmin(admin.ModelAdmin):
    list_display = ("organization", "legal_name", "email", "phone")
    search_fields = ("organization__name", "legal_name", "email", "phone")
