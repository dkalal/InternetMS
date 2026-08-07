from django.contrib import admin
from users.admin_scoping import TenantScopedAdmin

from .models import IntegrationConsumer


@admin.register(IntegrationConsumer)
class IntegrationConsumerAdmin(TenantScopedAdmin):
    list_display = ('name', 'organization', 'user', 'is_active', 'updated_at')
    list_filter = ('is_active', 'organization')
    search_fields = ('name', 'organization__name', 'user__username')
