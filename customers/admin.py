from django.contrib import admin

from .models import Customer, CustomerDocument, CustomerSite, InternetCustomer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "customer_type", "status", "pricing_tier", "is_deleted")
    list_filter = ("organization", "customer_type", "status", "pricing_tier", "is_deleted")
    search_fields = ("name", "email", "phone", "location")


@admin.register(CustomerSite)
class CustomerSiteAdmin(admin.ModelAdmin):
    list_display = ("customer", "name", "location", "is_primary", "is_active")
    list_filter = ("organization", "is_primary", "is_active")
    search_fields = ("customer__name", "name", "location", "ip_address", "vlan_id")


@admin.register(InternetCustomer)
class InternetCustomerAdmin(admin.ModelAdmin):
    list_display = ("customer", "package_type", "start_date", "end_date")
    search_fields = ("customer__name",)


@admin.register(CustomerDocument)
class CustomerDocumentAdmin(admin.ModelAdmin):
    list_display = ("customer", "document_type", "date_issued", "amount")
    list_filter = ("organization", "document_type", "date_issued")
    search_fields = ("customer__name",)
