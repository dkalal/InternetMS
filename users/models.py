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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        model = globals().get("TenantMembership")
        if model is not None:
            role = (
                model.BaseRole.SUPER_ADMIN if self.user.is_superuser else
                model.BaseRole.ADMIN_MANAGER if self.role in {self.Role.OWNER, self.Role.ADMIN} else
                model.BaseRole.SALES
            )
            model.objects.update_or_create(
                tenant=self.organization, user=self.user,
                defaults={"base_role": role, "is_active": self.is_active},
            )


class OrganizationBranding(models.Model):
    tenant = models.OneToOneField(
        Organization, on_delete=models.PROTECT, related_name="tenant_branding",
    )
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

    def save(self, *args, **kwargs):
        self.tenant_id = self.organization_id
        super().save(*args, **kwargs)


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
        model = globals().get("TenantMembership")
        if model is not None and self.tenant_id is not None:
            role = (
                model.BaseRole.ADMIN_MANAGER
                if self.role == self.Role.TENANT_ADMIN
                else model.BaseRole.SALES
            )
            model.objects.update_or_create(
                tenant=self.tenant, user=self.user,
                defaults={"base_role": role, "is_active": self.user.is_active},
            )
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


class TenantMembership(models.Model):
    """The authoritative link between a global user and a tenant."""

    class BaseRole(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "Super Administrator"
        ADMIN_MANAGER = "ADMIN_MANAGER", "Administrator / Manager"
        SALES = "SALES", "Sales"
        TECHNICIAN = "TECHNICIAN", "Technician"

    tenant = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="tenant_memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tenant_memberships",
    )
    base_role = models.CharField(max_length=20, choices=BaseRole.choices, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    invited_by = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invited_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user"], name="unique_tenant_membership"),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["tenant", "base_role", "is_active"]),
        ]

    def clean(self):
        if self.invited_by_id and self.invited_by.tenant_id != self.tenant_id:
            raise ValidationError("Inviter and membership must belong to the same tenant.")

    def __str__(self) -> str:
        return f"{self.user} @ {self.tenant} ({self.base_role})"

    @property
    def granted_action_codes(self):
        return list(self.permission_grants.values_list("action_code", flat=True))


class TenantPermissionGrant(models.Model):
    class Scope(models.TextChoices):
        OWN = "OWN", "Own records"
        ASSIGNED = "ASSIGNED", "Own and assigned records"
        TENANT_ALL = "TENANT_ALL", "All tenant records"

    membership = models.ForeignKey(
        TenantMembership,
        on_delete=models.CASCADE,
        related_name="permission_grants",
    )
    action_code = models.CharField(max_length=100, db_index=True)
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.OWN)
    granted_by = models.ForeignKey(
        TenantMembership,
        on_delete=models.PROTECT,
        related_name="permission_grants_made",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["membership", "action_code"], name="unique_membership_permission_grant"),
        ]
        indexes = [models.Index(fields=["membership", "action_code", "scope"])]

    def clean(self):
        if self.granted_by_id and self.granted_by.tenant_id != self.membership.tenant_id:
            raise ValidationError("Permission grants cannot cross tenants.")
        if self.granted_by_id == self.membership_id:
            raise ValidationError("Users cannot grant permissions to themselves.")


class SupportAccessSession(models.Model):
    """An explicit, time-bounded Super Administrator tenant context."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="support_access_sessions",
    )
    tenant = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="support_access_sessions")
    reason = models.CharField(max_length=500)
    session_key = models.CharField(max_length=40, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["actor", "session_key", "ended_at"])]

    @property
    def is_active(self):
        return self.ended_at is None
