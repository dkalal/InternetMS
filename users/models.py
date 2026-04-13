from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.core.exceptions import ValidationError


class Organization(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:80] or 'org'
        super().save(*args, **kwargs)


class Tenant(Organization):
    class Meta:
        proxy = True
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        ADMIN = 'admin', 'Admin'
        MEMBER = 'member', 'Member'
        VIEWER = 'viewer', 'Viewer'

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'user'], name='unique_org_member'),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['organization', 'is_active']),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.organization} ({self.role})"


class OrganizationBranding(models.Model):
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="branding",
        primary_key=True,
    )
    legal_name = models.CharField(max_length=200, blank=True, default="")
    address_line1 = models.CharField(max_length=200, blank=True, default="")
    address_line2 = models.CharField(max_length=200, blank=True, default="")
    phone = models.CharField(max_length=100, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    tin_number = models.CharField(max_length=50, blank=True, default="", verbose_name="TIN Number")
    vrn_number = models.CharField(max_length=50, blank=True, default="", verbose_name="VAT Reg. No. (VRN)")
    bank_details = models.TextField(blank=True, default="")
    footer_note = models.TextField(blank=True, default="")
    logo = models.ImageField(upload_to="org_logos/", blank=True, null=True)

    def __str__(self) -> str:
        return f"Branding: {self.organization}"


class UserAccessProfile(models.Model):
    class Role(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
        TENANT_ADMIN = "TENANT_ADMIN", "Tenant Admin"
        TENANT_STAFF = "TENANT_STAFF", "Tenant Staff"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access_profile")
    tenant = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="user_access_profiles",
        null=True,
        blank=True,
        db_index=True,
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.TENANT_STAFF, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "role"]),
            models.Index(fields=["role", "updated_at"]),
        ]

    def clean(self):
        if self.role == self.Role.SUPER_ADMIN and self.tenant_id is not None:
            raise ValidationError("SUPER_ADMIN must not be bound to a tenant.")
        if self.role != self.Role.SUPER_ADMIN and self.tenant_id is None:
            raise ValidationError("Tenant is required for non-super-admin users.")

    def save(self, *args, **kwargs):
        self.full_clean()
        old_role = None
        old_tenant_id = None
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only("role", "tenant_id").first()
            if previous is not None:
                old_role = previous.role
                old_tenant_id = previous.tenant_id
        super().save(*args, **kwargs)
        role_changed = old_role is not None and old_role != self.role
        tenant_changed = old_tenant_id is not None and old_tenant_id != self.tenant_id
        if role_changed or tenant_changed:
            from audit.models import AuditLog

            log_tenant = self.tenant
            if log_tenant is None:
                log_tenant = Organization.objects.order_by("id").first()
            if log_tenant is not None:
                AuditLog.objects.create(
                    organization=log_tenant,
                    actor=self.user,
                    action="security.user_access.changed",
                    object_type="User",
                    object_id=str(self.user_id),
                    metadata={
                        "old_role": old_role,
                        "new_role": self.role,
                        "old_tenant_id": old_tenant_id,
                        "new_tenant_id": self.tenant_id,
                    },
                )

    def __str__(self) -> str:
        return f"{self.user} ({self.role})"
