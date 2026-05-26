from django.contrib import admin

from .models import IntegrationConsumer


@admin.register(IntegrationConsumer)
class IntegrationConsumerAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'user', 'is_active', 'updated_at')
    list_filter = ('is_active', 'organization')
    search_fields = ('name', 'organization__name', 'user__username')
