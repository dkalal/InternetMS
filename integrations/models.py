from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class IntegrationConsumer(models.Model):
    tenant = models.ForeignKey(
        'users.Organization', on_delete=models.PROTECT, related_name='tenant_integration_consumers',
        db_index=True,
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='integration_consumer',
    )
    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='integration_consumers',
        db_index=True,
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True, db_index=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['organization__name', 'name']
        indexes = [
            models.Index(fields=['organization', 'is_active']),
        ]

    def __str__(self) -> str:
        return f'{self.name} @ {self.organization}'

    def clean(self):
        super().clean()
        profile = getattr(self.user, 'access_profile', None)
        if profile is None:
            return
        if profile.tenant_id != self.organization_id:
            raise ValidationError('Integration consumer user must belong to the same tenant.')

    def save(self, *args, **kwargs):
        self.tenant_id = self.organization_id
        self.full_clean()
        super().save(*args, **kwargs)


class ExternalAssetReference(models.Model):
    """Read-only projection of an AssetMS asset linked to a local customer."""

    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_external_asset_references',
        db_index=True,
    )
    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='external_asset_references',
        db_index=True,
    )
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.CASCADE,
        related_name='external_assets',
    )
    external_uuid = models.UUIDField()
    display_name = models.CharField(max_length=200, blank=True)
    asset_tag = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=100, blank=True)
    category_name = models.CharField(max_length=200)
    branch_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=32)
    description = models.TextField(blank=True)
    custom_attributes = models.JSONField(default=list, blank=True)
    source_url = models.URLField(blank=True)
    source_updated_at = models.DateTimeField()
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category_name', 'asset_tag', 'external_uuid']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'external_uuid'],
                name='uniq_ext_asset_per_tenant',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'customer'], name='extasset_tenant_customer'),
            models.Index(fields=['tenant', 'status'], name='extasset_tenant_status'),
        ]

    def __str__(self):
        return self.display_name or self.asset_tag or self.serial_number or f'{self.category_name} ({self.external_uuid})'

    @property
    def status_label(self):
        return {
            'active': 'Active',
            'in_maintenance': 'In maintenance',
            'retired': 'Retired',
            'lost': 'Lost',
            'deleted': 'Deleted',
            'transferred': 'In transfer',
        }.get(self.status, self.status.replace('_', ' ').title())

    def clean(self):
        super().clean()
        if self.organization_id and self.tenant_id != self.organization_id:
            raise ValidationError('External asset tenant and organization must match.')
        if self.customer_id and self.customer.tenant_id != self.tenant_id:
            raise ValidationError('External asset customer must belong to the same tenant.')

    def save(self, *args, **kwargs):
        self.tenant_id = self.organization_id
        self.full_clean()
        super().save(*args, **kwargs)
