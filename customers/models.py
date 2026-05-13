from django.db import models
from django.urls import reverse
from django.conf import settings
from django.core.validators import RegexValidator
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
        RETAIL = 'retail', 'Retail'
        WHOLESALE = 'wholesale', 'Wholesale'
        CORPORATE = 'corporate', 'Corporate'
        VIP = 'vip', 'VIP'
    
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
        null=True,
        blank=True,
        db_index=True,
    )
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

class InternetCustomer(models.Model):
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='internet_profile')
    tenant = models.ForeignKey(
        'users.Organization',
        on_delete=models.PROTECT,
        related_name='tenant_internet_customers',
        null=True,
        blank=True,
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
        null=True,
        blank=True,
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
        super().save(*args, **kwargs)
