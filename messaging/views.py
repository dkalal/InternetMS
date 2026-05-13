from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from billing.models import BillingDocument
from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization

from .models import MessageTemplate
from .services import (
    MessageBuilderService,
    MessagingServiceError,
    available_templates,
    categories_for_source,
    get_template_for_tenant,
    send_whatsapp_message,
    source_context,
)


def _error(message: str, *, status: int = 400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _request_source(request):
    doc_type = request.GET.get("doc_type") or request.POST.get("doc_type") or ""
    doc_id = request.GET.get("doc_id") or request.POST.get("doc_id") or None
    customer_id = request.GET.get("customer") or request.POST.get("customer") or request.POST.get("customer_id") or None
    try:
        doc_id = int(doc_id) if doc_id else None
        customer_id = int(customer_id) if customer_id else None
    except (TypeError, ValueError):
        raise MessagingServiceError("Invalid source identifiers.")
    return customer_id, doc_type, doc_id


@login_required
@require_GET
def template_options(request):
    organization = require_organization(request)
    require_permission(request, PermissionCode.WHATSAPP_SEND)
    category = request.GET.get("category") or ""
    _, doc_type, _ = _request_source(request)
    templates = available_templates(organization=organization, category=categories_for_source(category=category, doc_type=doc_type))
    return JsonResponse(
        {
            "ok": True,
            "templates": [
                {
                    "id": template.id,
                    "name": template.name,
                    "category": template.category,
                    "scope": "tenant" if template.tenant_id else "global",
                }
                for template in templates
            ],
        }
    )


@login_required
@require_POST
def preview_message(request):
    organization = require_organization(request)
    require_permission(request, PermissionCode.WHATSAPP_SEND)
    try:
        template = get_template_for_tenant(organization=organization, template_id=int(request.POST.get("template_id") or 0))
        customer_id, doc_type, doc_id = _request_source(request)
        customer, _, context = source_context(
            organization=organization,
            customer_id=customer_id,
            doc_type=doc_type,
            doc_id=doc_id,
            request=request,
        )
        message = MessageBuilderService.render(template=template, context=context)
    except (MessagingServiceError, ValueError) as exc:
        return _error(str(exc))
    except PermissionDenied:
        raise
    return JsonResponse(
        {
            "ok": True,
            "message": message,
            "customer": {"id": customer.id, "name": customer.name, "phone": customer.phone or ""},
        }
    )


@login_required
@require_POST
def send_manual_message(request):
    organization = require_organization(request)
    require_permission(request, PermissionCode.WHATSAPP_SEND)
    try:
        customer_id, doc_type, doc_id = _request_source(request)
        customer, document, _ = source_context(
            organization=organization,
            customer_id=customer_id,
            doc_type=doc_type,
            doc_id=doc_id,
            request=request,
        )
        template = None
        template_id = request.POST.get("template_id")
        if template_id:
            template = get_template_for_tenant(organization=organization, template_id=int(template_id))
        phone = request.POST.get("phone") or customer.phone or ""
        message = request.POST.get("message") or ""
        related_object_type = ""
        related_object_id = ""
        if document is not None:
            related_object_type = document.document_type
            related_object_id = str(document.id)
        result = send_whatsapp_message(
            organization=organization,
            customer=customer,
            phone=phone,
            message=message,
            actor=request.user,
            template=template,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
            mode="manual",
        )
    except (MessagingServiceError, ValueError) as exc:
        return _error(str(exc))
    except PermissionDenied:
        raise
    return JsonResponse({"ok": True, "url": result.url, "log_id": result.log.id})
