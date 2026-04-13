from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "action", "object_type", "object_id", "actor")
    list_filter = ("organization", "action", "object_type")
    search_fields = ("object_id", "metadata")
    readonly_fields = ("organization", "actor", "action", "object_type", "object_id", "metadata", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

