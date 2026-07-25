from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import F, Q
from custom_fields.mixins import CustomFieldPageContextMixin
from .models import Product, ProductCategory
from .forms import ProductForm
from custom_fields.services import CustomFieldService
from users.permissions import PermissionCode, require_permission
from inventory.services import audit
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
        "category": ("catalog_category__name", "category", "name"),
        "-category": ("-catalog_category__name", "-category", "name"),
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
        require_permission(self.request, PermissionCode.PRODUCT_VIEW)
        queryset = super().get_queryset().filter(organization=organization).select_related(
            "customer", "catalog_category", "inventory_balance"
        )
        search = (self.request.GET.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(sku__icontains=search)
                | Q(brand__icontains=search)
                | Q(model_number__icontains=search)
                | Q(customer__name__icontains=search)
                | Q(stock_units__serial_number__icontains=search)
            ).distinct()
        catalog_category = self.request.GET.get("catalog_category")
        if catalog_category and catalog_category.isdigit():
            queryset = queryset.filter(catalog_category_id=catalog_category)
        legacy_category = self.request.GET.get("category")
        if legacy_category in dict(Product.CATEGORY_CHOICES):
            queryset = queryset.filter(category=legacy_category)
        item_type = self.request.GET.get("item_type")
        if item_type in Product.ItemType.values:
            queryset = queryset.filter(item_type=item_type)
        is_active = self.request.GET.get('is_active')
        if is_active in {"0", "1"}:
            queryset = queryset.filter(is_active=bool(int(is_active)))
        stock_state = self.request.GET.get("stock_state")
        if stock_state == "out":
            queryset = queryset.filter(
                item_type=Product.ItemType.PHYSICAL, track_stock=True, quantity__lte=0
            )
        elif stock_state == "low":
            queryset = queryset.filter(
                item_type=Product.ItemType.PHYSICAL,
                track_stock=True,
                quantity__gt=0,
            ).filter(
                Q(quantity__lte=F("reorder_threshold"))
                | Q(reorder_threshold=0, quantity__lte=5)
            )
        elif stock_state == "available":
            queryset = queryset.filter(
                item_type=Product.ItemType.PHYSICAL, track_stock=True, quantity__gt=0
            )
        serialized = self.request.GET.get("serialized")
        if serialized in {"0", "1"}:
            queryset = queryset.filter(is_serialized=bool(int(serialized)))
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
        context["catalog_categories"] = ProductCategory.objects.filter(
            tenant=require_organization(self.request), is_active=True
        ).order_by("name")
        context.update(page_context(self.request, context["page_obj"], page_size=self.get_paginate_by(self.object_list)))
        return context

class ProductDetailView(CustomFieldPageContextMixin, LoginRequiredMixin, DetailView):
    model = Product
    template_name = 'products/product_detail.html'
    context_object_name = 'product'
    custom_field_target_model = "product"

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_VIEW)
        return super().get_queryset().filter(organization=organization)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["custom_fields"] = CustomFieldService.get_custom_field_values(self.object)
        context.update(self.get_custom_field_modal_context(target_model="product"))
        return context

class ProductCreateView(CustomFieldPageContextMixin, LoginRequiredMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = 'products/product_form.html'
    success_url = reverse_lazy('product-list')
    custom_field_target_model = "product"
    custom_field_inline_use = True

    def dispatch(self, request, *args, **kwargs):
        require_organization(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_MANAGE)
        form.instance.organization = organization
        form.instance.tenant = organization
        form.instance.quantity = 0
        form.instance.stock = 0
        with transaction.atomic():
            response = super().form_valid(form)
            CustomFieldService.save_custom_field_values(self.object, form.cleaned_data, user=self.request.user)
            audit(
                organization=organization,
                actor=self.request.user,
                action='inventory.product.created',
                obj=self.object,
                new_value={'sku': self.object.sku, 'name': self.object.name, 'item_type': self.object.item_type},
            )
            legacy_quantity = self.request.POST.get('quantity')
            if legacy_quantity and self.object.track_stock:
                from decimal import Decimal
                from inventory.services import InventoryService

                if Decimal(legacy_quantity) > 0:
                    InventoryService.adjust_stock(
                        organization=organization,
                        product_id=self.object.pk,
                        quantity_delta=Decimal(legacy_quantity),
                        reason='opening_balance',
                        notes='Opening balance supplied by the legacy product form.',
                        actor=self.request.user,
                    )
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_custom_field_modal_context(target_model="product"))
        context["has_movement_history"] = getattr(context["form"], "has_movement_history", False)
        return context

class ProductUpdateView(CustomFieldPageContextMixin, LoginRequiredMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = 'products/product_form.html'
    success_url = reverse_lazy('product-list')
    custom_field_target_model = "product"
    custom_field_inline_use = True

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_MANAGE)
        return super().get_queryset().filter(organization=organization)

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_MANAGE)
        previous = Product.objects.unscoped().get(pk=self.object.pk)
        old_value = {
            'sku': previous.sku,
            'name': previous.name,
            'selling_price': str(previous.selling_price),
            'track_stock': previous.track_stock,
            'is_serialized': previous.is_serialized,
            'is_active': previous.is_active,
        }
        form.instance.organization = organization
        form.instance.tenant = organization
        with transaction.atomic():
            response = super().form_valid(form)
            CustomFieldService.save_custom_field_values(self.object, form.cleaned_data, user=self.request.user)
            audit(
                organization=organization,
                actor=self.request.user,
                action='inventory.product.updated',
                obj=self.object,
                old_value=old_value,
                new_value={
                    'sku': self.object.sku,
                    'name': self.object.name,
                    'selling_price': str(self.object.selling_price),
                    'track_stock': self.object.track_stock,
                    'is_serialized': self.object.is_serialized,
                    'is_active': self.object.is_active,
                },
            )
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_custom_field_modal_context(target_model="product"))
        context["has_movement_history"] = getattr(context["form"], "has_movement_history", False)
        return context

class ProductDeleteView(LoginRequiredMixin, DeleteView):
    model = Product
    template_name = 'products/product_confirm_delete.html'
    success_url = reverse_lazy('product-list')

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_MANAGE)
        return super().get_queryset().filter(organization=organization)
