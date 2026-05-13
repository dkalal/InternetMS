from django.db import models

from users.tenant_models import TenantScopedManager

class Package(models.Model):
    PACKAGE_TYPE_CHOICES = [
        ('indoor', 'Indoor Package'),
        ('outdoor', 'Outdoor Package'),
    ]
    
    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='packages',
        null=True,
        blank=True,
        db_index=True,
    )
    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_packages',
        null=True,
        blank=True,
        db_index=True,
    )
    name = models.CharField(max_length=100)
    package_type = models.CharField(max_length=20, choices=PACKAGE_TYPE_CHOICES)
    speed = models.CharField(max_length=50)  # e.g., "10 Mbps"
    monthly_fee = models.DecimalField(max_digits=10, decimal_places=2)
    setup_fee = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  
    is_active = models.BooleanField(default=True)
    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["organization", "package_type", "is_active"], name="services_org_type_active_idx"),
            models.Index(fields=["tenant", "package_type", "is_active"], name="services_ten_type_active_idx"),
            models.Index(fields=["organization", "name"], name="services_org_name_idx"),
            models.Index(fields=["organization", "monthly_fee"], name="services_org_monthly_idx"),
        ]
    
    @property
    def price(self):
        return self.monthly_fee + self.setup_fee

    def __str__(self):
        return f"{self.name} ({self.get_package_type_display()})"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        super().save(*args, **kwargs)
