from django.shortcuts import render
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Count, Q, Sum
from .models import Package
from .forms import PackageForm
from billing.models import CustomerSubscription, SubscriptionPeriod
from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization

class PackageListView(LoginRequiredMixin, ListView):
    model = Package
    template_name = 'services/package_list.html'
    context_object_name = 'packages'
    
    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        queryset = super().get_queryset().filter(organization=organization)
        package_type = self.request.GET.get('type')
        
        if package_type:
            queryset = queryset.filter(package_type=package_type)
            
        return queryset.annotate(
            active_subscribers=Count(
                "subscriptions",
                filter=Q(subscriptions__status=CustomerSubscription.Status.ACTIVE),
                distinct=True,
            ),
            unpaid_periods=Count(
                "subscriptions__periods",
                filter=Q(subscriptions__periods__status__in=[
                    SubscriptionPeriod.Status.INVOICED,
                    SubscriptionPeriod.Status.OVERDUE,
                ]),
                distinct=True,
            ),
        )

class PackageDetailView(LoginRequiredMixin, DetailView):
    model = Package
    template_name = 'services/package_detail.html'

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        return super().get_queryset().filter(organization=organization)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        package = self.object
        organization = require_organization(self.request)
        subscriptions = CustomerSubscription.objects.filter(
            organization=organization,
            package=package,
        ).select_related("customer")
        context["subscriptions"] = subscriptions
        context["active_subscriber_count"] = subscriptions.filter(status=CustomerSubscription.Status.ACTIVE).count()
        periods = SubscriptionPeriod.objects.filter(organization=organization, subscription__package=package)
        context["unpaid_period_count"] = periods.filter(
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE]
        ).count()
        context["collected_amount"] = periods.filter(status=SubscriptionPeriod.Status.PAID).aggregate(total=Sum("final_amount"))["total"] or 0
        return context

class PackageCreateView(LoginRequiredMixin, CreateView):
    model = Package
    form_class = PackageForm
    template_name = 'services/package_form.html'
    success_url = reverse_lazy('package-list')
    
    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        form.instance.organization = organization
        form.instance.tenant = organization
        messages.success(self.request, f'Package {form.instance.name} created successfully.')
        return super().form_valid(form)

class PackageUpdateView(LoginRequiredMixin, UpdateView):
    model = Package
    form_class = PackageForm
    template_name = 'services/package_form.html'
    success_url = reverse_lazy('package-list')

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        return super().get_queryset().filter(organization=organization)
    
    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        form.instance.organization = organization
        form.instance.tenant = organization
        messages.success(self.request, f'Package {form.instance.name} updated successfully.')
        return super().form_valid(form)

class PackageDeleteView(LoginRequiredMixin, DeleteView):
    model = Package
    template_name = 'services/package_confirm_delete.html'
    success_url = reverse_lazy('package-list')

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        return super().get_queryset().filter(organization=organization)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add any additional context if needed
        return context
    
    def delete(self, request, *args, **kwargs):
        package = self.get_object()
        messages.success(self.request, f'Package {package.name} deleted successfully.')
        return super().delete(request, *args, **kwargs)
