from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from .models import Product
from .forms import ProductForm
from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization
from internetservices.listing import apply_sort, clean_page_size, page_context, positive_decimal

class ProductListView(LoginRequiredMixin, ListView):
    model = Product
    template_name = 'products/product_list.html'
    context_object_name = 'products'
    paginate_by = 25
    sort_options = {
        "name": ("name", "id"),
        "-name": ("-name", "-id"),
        "category": ("category", "name"),
        "-category": ("-category", "name"),
        "stock": ("quantity", "name"),
        "-stock": ("-quantity", "name"),
        "retail": ("retail_price", "selling_price", "name"),
        "-retail": ("-retail_price", "-selling_price", "name"),
        "wholesale": ("wholesale_price", "name"),
        "-wholesale": ("-wholesale_price", "name"),
    }

    def get_paginate_by(self, queryset):
        return clean_page_size(self.request.GET.get("page_size"), default=self.paginate_by)

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        queryset = super().get_queryset().filter(organization=organization).select_related("customer")
        search = self.request.GET.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(customer__name__icontains=search)
            )
        category = self.request.GET.get("category")
        if category:
            queryset = queryset.filter(category=category)
        is_active = self.request.GET.get('is_active')
        if is_active in {"0", "1"}:
            queryset = queryset.filter(is_active=bool(int(is_active)))
        stock_state = self.request.GET.get("stock_state")
        if stock_state == "out":
            queryset = queryset.filter(quantity__lte=0)
        elif stock_state == "low":
            queryset = queryset.filter(quantity__gt=0, quantity__lte=5)
        elif stock_state == "available":
            queryset = queryset.filter(quantity__gt=5)
        min_price = positive_decimal(self.request.GET.get("min_price"))
        max_price = positive_decimal(self.request.GET.get("max_price"))
        if min_price is not None:
            queryset = queryset.filter(Q(retail_price__gte=min_price) | Q(retail_price__isnull=True, selling_price__gte=min_price))
        if max_price is not None:
            queryset = queryset.filter(Q(retail_price__lte=max_price) | Q(retail_price__isnull=True, selling_price__lte=max_price))
        queryset, self.active_sort = apply_sort(queryset, self.request.GET.get("sort"), self.sort_options, "name")
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_sort"] = getattr(self, "active_sort", self.request.GET.get("sort", "name"))
        context["category_choices"] = Product.CATEGORY_CHOICES
        context.update(page_context(self.request, context["page_obj"], page_size=self.get_paginate_by(self.object_list)))
        return context

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
