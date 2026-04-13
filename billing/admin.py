from django.contrib import admin

from .models import BillingDocument, BillingLineItem, CustomerSubscription, DocumentSequence, Promotion, SubscriptionPeriod


class BillingLineItemInline(admin.TabularInline):
    model = BillingLineItem
    extra = 0
    fields = (
        "product",
        "package",
        "description",
        "quantity",
        "base_unit_price",
        "unit_price",
        "discount_amount",
        "pricing_mode",
        "billing_behavior",
        "line_total",
    )


@admin.register(BillingDocument)
class BillingDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "document_type",
        "number",
        "version_number",
        "is_current_version",
        "customer",
        "issue_date",
        "status",
        "total",
    )
    list_filter = ("organization", "document_type", "status")
    search_fields = ("number", "customer__name")
    inlines = [BillingLineItemInline]


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(admin.ModelAdmin):
    list_display = ("organization", "tenant", "document_type", "sequence_date", "last_number")
    list_filter = ("organization", "document_type", "sequence_date")


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ("organization", "name", "applies_to", "reward_type", "reward_value", "is_active")
    list_filter = ("organization", "applies_to", "reward_type", "is_active")
    search_fields = ("name",)


@admin.register(CustomerSubscription)
class CustomerSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "customer", "package", "status", "monthly_fee_at_signup", "paid_through_date")
    list_filter = ("organization", "status", "package")
    search_fields = ("customer__name", "package__name")


@admin.register(SubscriptionPeriod)
class SubscriptionPeriodAdmin(admin.ModelAdmin):
    list_display = ("organization", "subscription", "period_start", "period_end", "status", "final_amount", "invoice", "receipt")
    list_filter = ("organization", "status", "period_start")
    search_fields = ("subscription__customer__name", "subscription__package__name", "invoice__number", "receipt__number")
