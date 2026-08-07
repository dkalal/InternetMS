from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import CreateView, ListView, TemplateView, UpdateView, View

from users.models import Organization
from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization

from .forms_model import CustomFieldDefinitionForm
from .models import CustomFieldDefinition
from .services import CustomFieldService


class CustomFieldTenantMixin(LoginRequiredMixin):
    permission_code = PermissionCode.CUSTOM_FIELD_MANAGE

    def get_organization(self):
        return require_organization(self.request)

    def dispatch(self, request, *args, **kwargs):
        require_permission(request, self.permission_code)
        self.organization = self.get_organization()
        return super().dispatch(request, *args, **kwargs)


class CustomFieldOrganizationSelectView(CustomFieldTenantMixin, TemplateView):
    template_name = "custom_fields/custom_field_org_select.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["organizations"] = Organization.objects.filter(is_active=True).order_by("name", "id")
        return context


class CustomFieldDefinitionListView(CustomFieldTenantMixin, ListView):
    model = CustomFieldDefinition
    template_name = "custom_fields/custom_field_list.html"
    context_object_name = "custom_fields"

    def get_queryset(self):
        if self.organization is None:
            return CustomFieldDefinition.objects.none()
        return (
            CustomFieldDefinition.objects.filter(organization=self.organization)
            .select_related("created_by")
            .order_by("target_model", "display_order", "label", "id")
        )

    def get(self, request, *args, **kwargs):
        if self.organization is None:
            return redirect("custom-field-org-select")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["organization"] = self.organization
        context["target_model_choices"] = CustomFieldDefinition.TargetModel.choices
        context["organizations"] = Organization.objects.filter(is_active=True).order_by("name", "id")
        return context


class CustomFieldDefinitionCreateView(CustomFieldTenantMixin, CreateView):
    model = CustomFieldDefinition
    form_class = CustomFieldDefinitionForm
    template_name = "custom_fields/custom_field_form.html"
    success_url = reverse_lazy("custom-field-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = self.organization
        return kwargs

    def form_valid(self, form):
        form.instance.organization = self.organization
        form.instance.tenant = self.organization
        form.instance.created_by = self.request.user
        messages.success(self.request, f"Custom field {form.instance.label} created successfully.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("custom-field-list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["organization"] = self.organization
        context["page_title"] = "New custom field"
        return context


class CustomFieldInlineCreateView(CustomFieldTenantMixin, CreateView):
    model = CustomFieldDefinition
    form_class = CustomFieldDefinitionForm

    def dispatch(self, request, *args, **kwargs):
        if request.method != "POST":
            return redirect("custom-field-list")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = self.organization
        return kwargs

    def _next_url(self):
        fallback = str(reverse_lazy("custom-field-list"))
        next_url = self.request.POST.get("next") or self.request.META.get("HTTP_REFERER") or fallback
        if url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        return fallback

    def form_valid(self, form):
        form.instance.organization = self.organization
        form.instance.tenant = self.organization
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        is_xhr = self.request.headers.get("x-requested-with") == "XMLHttpRequest"
        if not is_xhr:
            messages.success(self.request, f"Custom field {form.instance.label} created successfully.")
        if is_xhr:
            field_html = ""
            if self.object and self.object.target_model == self.request.POST.get("target_model"):
                field_html = CustomFieldService.render_custom_field_input_html(self.object)
            return JsonResponse(
                {
                    "ok": True,
                    "definition": {
                        "id": self.object.id,
                        "key": self.object.key,
                        "label": self.object.label,
                        "target_model": self.object.target_model,
                        "field_html": field_html,
                    },
                    "message": f"Custom field {self.object.label} created successfully.",
                }
            )
        return response

    def get_success_url(self):
        return self._next_url()

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "ok": False,
                    "errors": form.errors.get_json_data(),
                    "non_field_errors": list(form.non_field_errors()),
                },
                status=400,
            )
        token = uuid.uuid4().hex
        self.request.session[f"custom_field_modal:{token}"] = form.data.dict()
        self.request.session.modified = True
        next_url = self._next_url()
        separator = "&" if "?" in next_url else "?"
        return redirect(f"{next_url}{separator}cf_modal={token}")


class CustomFieldDefinitionUpdateView(CustomFieldTenantMixin, UpdateView):
    model = CustomFieldDefinition
    form_class = CustomFieldDefinitionForm
    template_name = "custom_fields/custom_field_form.html"
    success_url = reverse_lazy("custom-field-list")

    def get_queryset(self):
        return CustomFieldDefinition.objects.filter(organization=self.organization)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = self.organization
        return kwargs

    def form_valid(self, form):
        form.instance.organization = self.organization
        form.instance.tenant = self.organization
        messages.success(self.request, f"Custom field {form.instance.label} updated successfully.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("custom-field-list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["organization"] = self.organization
        context["page_title"] = f"Edit {self.object.label}"
        return context


class CustomFieldDefinitionDeactivateView(CustomFieldTenantMixin, View):
    def post(self, request, *args, **kwargs):
        custom_field = get_object_or_404(CustomFieldDefinition, organization=self.organization, pk=kwargs["pk"])
        custom_field.is_active = False
        custom_field.save(update_fields=["is_active", "updated_at"])
        messages.success(request, f"Custom field {custom_field.label} deactivated.")
        return redirect(reverse_lazy("custom-field-list"))
