from django.contrib import admin

from .models import CustomFieldDefinition, CustomFieldValue


@admin.register(CustomFieldDefinition)
class CustomFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = ("label", "target_model", "key", "field_type", "is_active", "display_order", "organization")
    list_filter = ("organization", "target_model", "field_type", "is_active", "show_on_create", "show_on_edit", "show_on_detail")
    search_fields = ("label", "key", "help_text")
    ordering = ("organization", "target_model", "display_order", "label")


@admin.register(CustomFieldValue)
class CustomFieldValueAdmin(admin.ModelAdmin):
    list_display = ("field_definition", "target_model", "object_id", "organization", "updated_at")
    list_filter = ("organization", "target_model", "field_definition__field_type")
    search_fields = ("field_definition__label", "field_definition__key", "object_id")
    readonly_fields = ("organization", "tenant", "field_definition", "target_model", "object_id", "value_text", "value_number", "value_date", "value_boolean", "value_json")
