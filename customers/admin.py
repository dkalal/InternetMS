from django.contrib import admin
from users.admin_scoping import TenantScopedAdmin

from .models import Customer, CustomerDocument, CustomerSite, InternetCustomer, InternetService


@admin.register(Customer)
class CustomerAdmin(TenantScopedAdmin):
    list_display = ("name", "organization", "customer_type", "status", "pricing_tier", "is_deleted")
    list_filter = ("organization", "customer_type", "status", "pricing_tier", "is_deleted")
    search_fields = ("name", "email", "phone", "location")


@admin.register(CustomerSite)
class CustomerSiteAdmin(TenantScopedAdmin):
    list_display = ("customer", "name", "location", "is_primary", "is_active")
    list_filter = ("organization", "is_primary", "is_active")
    search_fields = ("customer__name", "name", "location", "ip_address", "vlan_id")


@admin.register(InternetCustomer)
class InternetCustomerAdmin(TenantScopedAdmin):
    list_display = ("customer", "package_type", "start_date", "end_date")


@admin.register(InternetService)
class InternetServiceAdmin(TenantScopedAdmin):
    list_display = ("service_code", "customer", "site", "operational_status", "ip_address", "vlan_id")
    list_filter = ("operational_status",)
    search_fields = ("service_code", "name", "customer__name", "site__name", "ip_address", "vlan_id")


@admin.register(CustomerDocument)
class CustomerDocumentAdmin(TenantScopedAdmin):
    list_display = ("customer", "document_type", "date_issued", "amount")
    list_filter = ("organization", "document_type", "date_issued")
    search_fields = ("customer__name",)
