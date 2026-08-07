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
