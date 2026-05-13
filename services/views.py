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
from internetservices.listing import apply_sort, clean_page_size, page_context, positive_decimal

class PackageListView(LoginRequiredMixin, ListView):
    model = Package
    template_name = 'services/package_list.html'
    context_object_name = 'packages'
    paginate_by = 25
    sort_options = {
        "name": ("name", "id"),
        "-name": ("-name", "-id"),
        "monthly_fee": ("monthly_fee", "name"),
        "-monthly_fee": ("-monthly_fee", "name"),
        "subscribers": ("active_subscribers", "name"),
        "-subscribers": ("-active_subscribers", "name"),
        "unpaid": ("unpaid_periods", "name"),
        "-unpaid": ("-unpaid_periods", "name"),
        "type": ("package_type", "name"),
        "-type": ("-package_type", "name"),
    }

    def get_paginate_by(self, queryset):
        return clean_page_size(self.request.GET.get("page_size"), default=self.paginate_by)
    
    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        queryset = super().get_queryset().filter(organization=organization)
        search = self.request.GET.get("search")
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search) | Q(speed__icontains=search))
        package_type = self.request.GET.get('type')
        
        if package_type:
            queryset = queryset.filter(package_type=package_type)
        is_active = self.request.GET.get("is_active")
        if is_active in {"0", "1"}:
            queryset = queryset.filter(is_active=is_active == "1")
        min_price = positive_decimal(self.request.GET.get("min_price"))
        max_price = positive_decimal(self.request.GET.get("max_price"))
        if min_price is not None:
            queryset = queryset.filter(monthly_fee__gte=min_price)
        if max_price is not None:
            queryset = queryset.filter(monthly_fee__lte=max_price)

        queryset = queryset.annotate(
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
        subscriber_state = self.request.GET.get("subscriber_state")
        if subscriber_state == "has":
            queryset = queryset.filter(active_subscribers__gt=0)
        elif subscriber_state == "none":
            queryset = queryset.filter(active_subscribers=0)
        unpaid_state = self.request.GET.get("unpaid_state")
        if unpaid_state == "has":
            queryset = queryset.filter(unpaid_periods__gt=0)
        elif unpaid_state == "none":
            queryset = queryset.filter(unpaid_periods=0)
        queryset, self.active_sort = apply_sort(queryset, self.request.GET.get("sort"), self.sort_options, "name")
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_sort"] = getattr(self, "active_sort", self.request.GET.get("sort", "name"))
        context["package_type_choices"] = Package.PACKAGE_TYPE_CHOICES
        context.update(page_context(self.request, context["page_obj"], page_size=self.get_paginate_by(self.object_list)))
        return context

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
