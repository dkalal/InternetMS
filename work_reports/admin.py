from django.contrib import admin

from .models import TechnicianWorkReport, WorkReportHistory


class SupportTenantAdminMixin:
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        tenant = getattr(request, "tenant", None)
        return queryset.filter(tenant=tenant) if tenant is not None else queryset.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TechnicianWorkReport)
class TechnicianWorkReportAdmin(SupportTenantAdminMixin, admin.ModelAdmin):
    list_display = ("work_title", "technician", "client_name", "service_date", "status")
    list_filter = ("status", "service_date")
    search_fields = ("work_title", "client_name", "technician__user__username")
    readonly_fields = tuple(field.name for field in TechnicianWorkReport._meta.fields)


@admin.register(WorkReportHistory)
class WorkReportHistoryAdmin(SupportTenantAdminMixin, admin.ModelAdmin):
    list_display = ("report", "event", "actor_membership", "created_at")
    list_filter = ("event",)
    readonly_fields = tuple(field.name for field in WorkReportHistory._meta.fields)
