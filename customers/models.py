import uuid as uuid_lib

from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.conf import settings
from django.core.validators import RegexValidator
from users.tenant_models import TenantScopedManager
from .managers import CustomerManager, AllCustomerManager


class Customer(models.Model):
    CUSTOMER_TYPE_CHOICES = [
        ('internet', 'Internet Customer'),
        ('random', 'Random Customer'),
    ]
    
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'
        SUSPENDED = 'suspended', 'Suspended'

    class PricingTier(models.TextChoices):
        RETAIL = 'retail', 'Standard'
        TECHNICIAN = 'technician', 'Technician'
        WHOLESALE = 'wholesale', 'Wholesale'

    @property
    def default_sale_pricing_category(self):
        """Canonical catalog category implied by this customer's pricing tier."""
        if self.pricing_tier == self.PricingTier.TECHNICIAN:
            return 'technician'
        if self.pricing_tier == self.PricingTier.WHOLESALE:
            return 'wholesale'
        return 'standard'
    
    # Basic Information
    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='customers',
        null=True,
        blank=True,
        db_index=True,
    )
    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_customers',
        db_index=True,
    )
    uuid = models.UUIDField(default=uuid_lib.uuid4, editable=False, unique=True, db_index=True)
    name = models.CharField(max_length=200, db_index=True)
    customer_type = models.CharField(max_length=20, choices=CUSTOMER_TYPE_CHOICES, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    pricing_tier = models.CharField(max_length=20, choices=PricingTier.choices, default=PricingTier.RETAIL, db_index=True)

    # Soft delete fields (never hard delete by default)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='deleted_customers',
    )
    
    # Contact Information
    email = models.EmailField(blank=True, null=True, db_index=True)
    phone_validator = RegexValidator(
        regex=r'^\+?1?\d{9,15}$',
        message="Phone number must be entered in the format: '+255123456789'. Up to 15 digits allowed."
    )
    phone = models.CharField(validators=[phone_validator], max_length=20, blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    location = models.CharField(max_length=255, db_index=True)
    
    # Network Information
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    vlan_id = models.CharField(max_length=50, blank=True, null=True)
    
    # Business Information
    tin_number = models.CharField(max_length=50, blank=True, null=True, verbose_name="TIN Number")
    vrn_number = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="VAT Reg. No. (VRN)",
        help_text="Optional. Used on invoices/receipts for VAT-registered customers.",
    )
    
    # Relationships
    packages = models.ManyToManyField('services.Package', related_name='customers', blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Custom Manager
    objects = CustomerManager()
    all_objects = AllCustomerManager()

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(pricing_tier__in=['retail', 'technician', 'wholesale']),
                name='customer_valid_pricing_tier',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'customer_type']),
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['name', 'customer_type']),
            models.Index(fields=['status', 'customer_type']),
            models.Index(fields=['email']),
            models.Index(fields=['organization', 'phone'], name="customers_org_phone_idx"),
            models.Index(fields=['organization', 'ip_address'], name="customers_org_ip_idx"),
            models.Index(fields=['organization', 'vlan_id'], name="customers_org_vlan_idx"),
        ]
        verbose_name = 'Customer'
        verbose_name_plural = 'Customers'
    
    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('customer-detail', args=[str(self.id)])
    
    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE and not self.is_deleted
    
    @property
    def is_internet_customer(self):
        return self.customer_type == 'internet'
    
    def get_full_contact(self):
        """Returns formatted contact information"""
        contact = []
        if self.email:
            contact.append(f"Email: {self.email}")
        if self.phone:
            contact.append(f"Phone: {self.phone}")
        return ' | '.join(contact) if contact else 'No contact info'

    @property
    def primary_site(self):
        primary = getattr(self, "_primary_site_cache", None)
        if primary is not None:
            return primary
        return self.sites.filter(is_primary=True).order_by("id").first() or self.sites.order_by("id").first()

    @property
    def primary_internet_service(self):
        site = self.primary_site
        if site is None:
            return None
        return site.internet_services.exclude(
            operational_status="disconnected"
        ).order_by("id").first()


class CustomerSite(models.Model):
    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='customer_sites',
        null=True,
        blank=True,
        db_index=True,
    )
    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_customer_sites',
        db_index=True,
    )
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='sites')
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=255, db_index=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    vlan_id = models.CharField(max_length=50, blank=True, null=True)
    notes = models.TextField(blank=True, default="")
    is_primary = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    packages = models.ManyToManyField('services.Package', related_name='customer_sites', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', 'name', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['customer'],
                condition=Q(is_primary=True),
                name='uniq_primary_site_per_customer',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'customer', 'is_active']),
            models.Index(fields=['organization', 'customer', 'is_primary']),
            models.Index(fields=['tenant', 'customer', 'is_active']),
        ]
        verbose_name = 'Customer Site'
        verbose_name_plural = 'Customer Sites'

    def __str__(self):
        return f"{self.customer.name} - {self.name}"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        if self.customer_id and self.customer.tenant_id != self.tenant_id:
            raise ValueError("Customer site belongs to another tenant.")
        super().save(*args, **kwargs)

    @property
    def display_label(self):
        return self.name if self.name else self.location

    @property
    def summary(self):
        parts = [self.location]
        if self.address:
            parts.append(self.address)
        return " · ".join(parts)


class InternetService(models.Model):
    """Installed Internet connection at one customer site.

    This is operational identity only. Package, agreed price, and commercial
    dates remain owned by CustomerSubscription.
    """

    class OperationalStatus(models.TextChoices):
        UNKNOWN = "unknown", "Status unknown"
        ACTIVE = "active", "Active"
        BLOCKED = "blocked", "Blocked"
        DISCONNECTED = "disconnected", "Disconnected"

    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='internet_services',
        db_index=True,
    )
    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_internet_services',
        db_index=True,
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='internet_services',
    )
    site = models.ForeignKey(
        CustomerSite,
        on_delete=models.PROTECT,
        related_name='internet_services',
    )
    service_code = models.CharField(max_length=64)
    name = models.CharField(max_length=120, default='Primary Internet Service')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    vlan_id = models.CharField(max_length=50, blank=True, null=True)
    operational_status = models.CharField(
        max_length=20,
        choices=OperationalStatus.choices,
        default=OperationalStatus.UNKNOWN,
        db_index=True,
    )
    installed_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    technical_notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        ordering = ['site_id', 'service_code', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'service_code'],
                name='uniq_internet_service_code_per_tenant',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'customer', 'operational_status'], name='cust_is_t_cust_stat_idx'),
            models.Index(fields=['tenant', 'site', 'operational_status'], name='cust_is_t_site_stat_idx'),
            models.Index(fields=['tenant', 'ip_address'], name='cust_is_t_ip_idx'),
            models.Index(fields=['tenant', 'vlan_id'], name='cust_is_t_vlan_idx'),
        ]

    def __str__(self):
        return f"{self.customer.name} · {self.site.name} · {self.service_code}"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            raise ValueError("Internet service organization and tenant must match.")
        if self.customer_id and self.customer.tenant_id != self.tenant_id:
            raise ValueError("Internet service customer belongs to another tenant.")
        if self.site_id:
            if self.site.tenant_id != self.tenant_id:
                raise ValueError("Internet service site belongs to another tenant.")
            if self.site.customer_id != self.customer_id:
                raise ValueError("Internet service site belongs to another customer.")
        if self.disconnected_at and self.installed_at and self.disconnected_at < self.installed_at:
            raise ValueError("Disconnected date cannot be earlier than installed date.")
        super().save(*args, **kwargs)

    @property
    def current_subscription(self):
        return self.subscriptions.filter(status="active").order_by("-start_date", "-id").first()

class InternetCustomer(models.Model):
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='internet_profile')
    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_internet_customers',
        db_index=True,
    )
    package_type = models.CharField(max_length=50, choices=[('indoor', 'Indoor'), ('outdoor', 'Outdoor')])
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    
    class Meta:
        verbose_name = 'Internet Customer Profile'
        verbose_name_plural = 'Internet Customer Profiles'
    
    def __str__(self):
        return f"{self.customer.name} - {self.package_type}"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.customer_id:
            self.tenant_id = self.customer.tenant_id or self.customer.organization_id
        if self.customer_id and self.customer.tenant_id != self.tenant_id:
            raise ValueError("Internet profile belongs to another tenant.")
        super().save(*args, **kwargs)
    
    @property
    def is_expired(self):
        """Check if subscription has expired"""
        if self.end_date:
            from django.utils import timezone
            return timezone.now().date() > self.end_date
        return False
    
    @property
    def days_remaining(self):
        """Calculate days remaining in subscription"""
        if self.end_date:
            from django.utils import timezone
            delta = self.end_date - timezone.now().date()
            return delta.days if delta.days > 0 else 0
        return None

class CustomerDocument(models.Model):
    DOCUMENT_TYPE_CHOICES = [
        ('quotation', 'Customer Quotation'),
        ('invoice', 'Customer Invoice'),
        ('receipt', 'Customer Receipt'),
    ]
    
    organization = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='customer_documents',
        null=True,
        blank=True,
        db_index=True,
    )
    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_customer_documents',
        db_index=True,
    )
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPE_CHOICES, db_index=True)
    file = models.FileField(upload_to='customer_documents/%Y/%m/')
    date_issued = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date_issued']
        indexes = [
            models.Index(fields=['organization', 'document_type']),
            models.Index(fields=['tenant', 'document_type']),
            models.Index(fields=['customer', 'document_type']),
            models.Index(fields=['date_issued']),
        ]
        verbose_name = 'Customer Document'
        verbose_name_plural = 'Customer Documents'
    
    def __str__(self):
        return f"{self.customer.name} - {self.get_document_type_display()} - {self.date_issued}"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        if self.customer_id and self.customer.tenant_id != self.tenant_id:
            raise ValueError("Customer document belongs to another tenant.")
        super().save(*args, **kwargs)
