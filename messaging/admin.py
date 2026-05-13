from django.contrib import admin

from .models import MessageTemplate, WhatsAppManualMessageLog


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "category", "is_active", "updated_at")
    list_filter = ("tenant", "category", "is_active")
    search_fields = ("name", "content")


@admin.register(WhatsAppManualMessageLog)
class WhatsAppManualMessageLogAdmin(admin.ModelAdmin):
    list_display = ("tenant", "customer", "phone_number", "template_used", "related_object_type", "status", "sent_by", "created_at")
    list_filter = ("tenant", "status", "related_object_type", "created_at")
    search_fields = ("customer__name", "phone_number", "message_content", "template_used__name")
    readonly_fields = ("created_at",)
