from django.db import models
from django.db.models import Q
from users.tenant_context import scope_queryset


class CustomerQuerySet(models.QuerySet):
    """Custom queryset for optimized queries"""

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def alive(self):
        return self.filter(is_deleted=False)
    
    def active(self):
        return self.filter(status='active')
    
    def inactive(self):
        return self.filter(status='inactive')
    
    def suspended(self):
        return self.filter(status='suspended')
    
    def internet_customers(self):
        return self.filter(customer_type='internet')
    
    def random_customers(self):
        return self.filter(customer_type='random')
    
    def with_packages(self):
        """Prefetch packages for performance"""
        return self.prefetch_related('packages')
    
    def with_internet_profile(self):
        """Select related internet profile"""
        return self.select_related('internet_profile')
    
    def with_documents(self):
        """Prefetch billing documents."""
        return self.prefetch_related('billing_documents')
    
    def optimized_list(self):
        """Optimized query for list views"""
        return self.select_related('internet_profile').prefetch_related('packages')
    
    def search(self, query):
        """Search customers by name, email, phone, location"""
        return self.filter(
            Q(name__icontains=query) |
            Q(email__icontains=query) |
            Q(phone__icontains=query) |
            Q(location__icontains=query) |
            Q(tin_number__icontains=query) |
            Q(vrn_number__icontains=query)
        )


class CustomerManager(models.Manager):
    """Custom manager for Customer model"""
    
    def get_queryset(self):
        queryset = CustomerQuerySet(self.model, using=self._db).alive()
        return scope_queryset(queryset, field_name="tenant")

    def with_deleted(self):
        return CustomerQuerySet(self.model, using=self._db)
    
    def active(self):
        return self.get_queryset().active()
    
    def inactive(self):
        return self.get_queryset().inactive()
    
    def suspended(self):
        return self.get_queryset().suspended()
    
    def internet_customers(self):
        return self.get_queryset().internet_customers()
    
    def random_customers(self):
        return self.get_queryset().random_customers()
    
    def search(self, query):
        return self.get_queryset().search(query)
    
    def optimized_list(self):
        return self.get_queryset().optimized_list()

    def for_organization(self, organization):
        return self.get_queryset().for_organization(organization)


class AllCustomerManager(models.Manager):
    def get_queryset(self):
        queryset = CustomerQuerySet(self.model, using=self._db)
        return scope_queryset(queryset, field_name="tenant")
