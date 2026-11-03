from django.contrib import admin

from .models import (
    TechnicianPaymentBatch, TechnicianPaymentRecord, TechnicianWorkReport, WorkReportHistory,
    WorkReportServiceDay,
)


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


@admin.register(WorkReportServiceDay)
class WorkReportServiceDayAdmin(SupportTenantAdminMixin, admin.ModelAdmin):
    list_display = ("report", "service_date", "activity_note")
    list_filter = ("service_date",)
    search_fields = ("report__work_title", "report__technician__user__username")
    readonly_fields = tuple(field.name for field in WorkReportServiceDay._meta.fields)


@admin.register(WorkReportHistory)
class WorkReportHistoryAdmin(SupportTenantAdminMixin, admin.ModelAdmin):
    list_display = ("report", "event", "actor_membership", "created_at")
    list_filter = ("event",)
    readonly_fields = tuple(field.name for field in WorkReportHistory._meta.fields)


@admin.register(TechnicianPaymentRecord)
class TechnicianPaymentRecordAdmin(SupportTenantAdminMixin, admin.ModelAdmin):
    list_display = ("report", "amount_paid", "payment_date", "payment_method", "status")
    list_filter = ("status", "payment_method", "payment_date")
    search_fields = (
        "report__work_title", "report__technician__user__username", "reference",
    )
    readonly_fields = tuple(field.name for field in TechnicianPaymentRecord._meta.fields)


@admin.register(TechnicianPaymentBatch)
class TechnicianPaymentBatchAdmin(SupportTenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id", "technician", "amount_paid_total", "payment_date", "payment_method", "status",
    )
    list_filter = ("status", "payment_method", "payment_date")
    search_fields = ("technician__user__username", "reference")
    readonly_fields = tuple(field.name for field in TechnicianPaymentBatch._meta.fields)
