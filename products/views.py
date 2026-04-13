from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import Product
from .forms import ProductForm
from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization

class ProductListView(LoginRequiredMixin, ListView):
    model = Product
    template_name = 'products/product_list.html'
    context_object_name = 'products'

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        queryset = super().get_queryset().filter(organization=organization)
        is_active = self.request.GET.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=bool(int(is_active)))
        return queryset

class ProductDetailView(LoginRequiredMixin, DetailView):
    model = Product
    template_name = 'products/product_detail.html'
    context_object_name = 'product'

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        return super().get_queryset().filter(organization=organization)

class ProductCreateView(LoginRequiredMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = 'products/product_form.html'
    success_url = reverse_lazy('product-list')

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        form.instance.organization = organization
        form.instance.tenant = organization
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

class ProductUpdateView(LoginRequiredMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = 'products/product_form.html'
    success_url = reverse_lazy('product-list')

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        return super().get_queryset().filter(organization=organization)

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        form.instance.organization = organization
        form.instance.tenant = organization
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

class ProductDeleteView(LoginRequiredMixin, DeleteView):
    model = Product
    template_name = 'products/product_confirm_delete.html'
    success_url = reverse_lazy('product-list')

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        return super().get_queryset().filter(organization=organization)
