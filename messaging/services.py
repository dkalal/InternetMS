from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse

from billing.models import BillingDocument
from customers.models import Customer

from .models import MessageTemplate, WhatsAppManualMessageLog


PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


class MessagingServiceError(Exception):
    pass


class MessageBuilderService:
    @classmethod
    def placeholders(cls, content: str) -> set[str]:
        return set(PLACEHOLDER_RE.findall(content or ""))

    @classmethod
    def render(cls, *, template: MessageTemplate, context: dict) -> str:
        content = template.content or ""
        required = cls.placeholders(content)
        optional = set((template.variables_schema or {}).get("optional", []))
        missing = sorted(
            name for name in required - optional if context.get(name) is None or str(context.get(name)).strip() == ""
        )
        if missing:
            raise MessagingServiceError(f"Missing template variables: {', '.join(missing)}")

        def replace(match):
            name = match.group(1)
            value = context.get(name, "")
            return "" if value is None else str(value)

        rendered = PLACEHOLDER_RE.sub(replace, content)
        if PLACEHOLDER_RE.search(rendered):
            raise MessagingServiceError("Message still contains unresolved placeholders.")
        return cls._normalize(rendered)

    @staticmethod
    def _normalize(message: str) -> str:
        lines = [" ".join(line.strip().split()) for line in message.replace("\r\n", "\n").split("\n")]
        normalized = "\n".join(lines)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()


class WhatsAppDispatcher:
    @classmethod
    def normalize_phone(cls, phone: str) -> str:
        digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
        if digits.startswith("0") and len(digits) == 10:
            digits = "255" + digits[1:]
        elif len(digits) == 9:
            digits = "255" + digits
        if not (9 <= len(digits) <= 15):
            raise MessagingServiceError("A valid WhatsApp phone number is required.")
        return digits

    @classmethod
    def build_manual_url(cls, *, phone: str, message: str) -> str:
        normalized_phone = cls.normalize_phone(phone)
        if not str(message or "").strip():
            raise MessagingServiceError("Message content is required.")
        return f"https://wa.me/{normalized_phone}?text={quote(message)}"


@dataclass(frozen=True)
class WhatsAppSendResult:
    log: WhatsAppManualMessageLog
    url: str


def _ensure_same_tenant(*, organization, customer: Customer, template: MessageTemplate | None = None):
    customer_tenant_id = customer.tenant_id or customer.organization_id
    if customer_tenant_id != organization.id:
        raise PermissionDenied("Cross-tenant customer access denied.")
    if template is not None and template.tenant_id not in {None, organization.id}:
        raise PermissionDenied("Cross-tenant template access denied.")


@transaction.atomic
def send_whatsapp_message(
    *,
    organization,
    customer: Customer,
    phone: str,
    message: str,
    actor,
    template: MessageTemplate | None = None,
    related_object_type: str = "",
    related_object_id: str = "",
    mode: str = "manual",
) -> WhatsAppSendResult:
    _ensure_same_tenant(organization=organization, customer=customer, template=template)
    if mode == "api":
        raise NotImplementedError("WhatsApp API dispatch is not implemented. Use manual mode.")
    if mode != "manual":
        raise MessagingServiceError(f"Unsupported WhatsApp dispatch mode: {mode}")

    normalized_phone = WhatsAppDispatcher.normalize_phone(phone)
    if not str(message or "").strip():
        raise MessagingServiceError("Message content is required.")

    log = WhatsAppManualMessageLog.objects.create(
        tenant=organization,
        customer=customer,
        phone_number=normalized_phone,
        message_content=message.strip(),
        template_used=template,
        related_object_type=related_object_type or "",
        related_object_id=str(related_object_id or ""),
        sent_by=actor,
        status=WhatsAppManualMessageLog.Status.SENT_MANUAL,
    )
    url = WhatsAppDispatcher.build_manual_url(phone=normalized_phone, message=log.message_content)
    log.status = WhatsAppManualMessageLog.Status.OPENED
    log.save(update_fields=["status"])
    return WhatsAppSendResult(log=log, url=url)


def available_templates(*, organization, category: str | list[str] | tuple[str, ...] | None = None):
    queryset = MessageTemplate.objects.unscoped().filter(is_active=True).filter(Q(tenant__isnull=True) | Q(tenant=organization))
    if category:
        if isinstance(category, (list, tuple, set)):
            queryset = queryset.filter(category__in=category)
        else:
            queryset = queryset.filter(category=category)
    return queryset.order_by("tenant_id", "name")


def get_template_for_tenant(*, organization, template_id: int) -> MessageTemplate:
    template = MessageTemplate.objects.unscoped().filter(pk=template_id, is_active=True).filter(
        Q(tenant__isnull=True) | Q(tenant=organization)
    ).first()
    if template is None:
        raise MessagingServiceError("Template not found.")
    return template


def _money(value, currency="TZS") -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        value = value.quantize(Decimal("0.01"))
    return f"{value:,.2f} {currency}"


def source_context(*, organization, customer_id: int | None = None, doc_type: str = "", doc_id: int | None = None, request=None):
    document = None
    if doc_type and doc_id:
        document = BillingDocument.objects.select_related("customer", "organization").filter(
            organization=organization,
            document_type=doc_type,
            pk=doc_id,
        ).first()
        if document is None:
            raise MessagingServiceError("Related billing document not found.")
        customer = document.customer
    elif customer_id:
        customer = Customer.objects.for_organization(organization).filter(pk=customer_id).first()
        if customer is None:
            raise MessagingServiceError("Customer not found.")
    else:
        raise MessagingServiceError("Customer or related document is required.")

    context = {
        "customer_name": customer.name,
        "customer_phone": customer.phone or "",
        "organization_name": organization.name,
        "amount": "",
        "currency": "",
        "due_date": "",
        "invoice_number": "",
        "quotation_number": "",
        "receipt_number": "",
        "document_number": "",
        "document_type": "",
        "pdf_url": "",
    }
    if document is not None:
        context.update(
            {
                "amount": _money(document.total, document.currency),
                "currency": document.currency,
                "due_date": document.due_date.isoformat() if document.due_date else "",
                "document_number": document.number,
                "document_type": document.get_document_type_display(),
            }
        )
        if document.document_type == BillingDocument.DocumentType.INVOICE:
            context["invoice_number"] = document.number
        elif document.document_type == BillingDocument.DocumentType.QUOTATION:
            context["quotation_number"] = document.number
        elif document.document_type == BillingDocument.DocumentType.RECEIPT:
            context["receipt_number"] = document.number
        if request is not None:
            context["pdf_url"] = request.build_absolute_uri(
                reverse("billing:document_pdf", kwargs={"doc_type": document.document_type, "pk": document.pk})
            )
    return customer, document, context


def category_for_source(*, doc_type: str = "") -> str:
    if doc_type == BillingDocument.DocumentType.INVOICE:
        return MessageTemplate.Category.INVOICE
    if doc_type == BillingDocument.DocumentType.QUOTATION:
        return MessageTemplate.Category.QUOTATION
    if doc_type == BillingDocument.DocumentType.RECEIPT:
        return MessageTemplate.Category.RECEIPT
    return MessageTemplate.Category.GENERAL


def categories_for_source(*, category: str = "", doc_type: str = "") -> list[str]:
    category = category or category_for_source(doc_type=doc_type)
    if category == MessageTemplate.Category.INVOICE:
        return [MessageTemplate.Category.INVOICE, MessageTemplate.Category.REMINDER, MessageTemplate.Category.GENERAL]
    if category == MessageTemplate.Category.QUOTATION:
        return [MessageTemplate.Category.QUOTATION, MessageTemplate.Category.GENERAL]
    if category == MessageTemplate.Category.RECEIPT:
        return [MessageTemplate.Category.RECEIPT, MessageTemplate.Category.GENERAL]
    if category == MessageTemplate.Category.GENERAL:
        return [MessageTemplate.Category.GENERAL, MessageTemplate.Category.SUPPORT, MessageTemplate.Category.REMINDER]
    return [category]


def validate_template_schema(schema: dict):
    if not isinstance(schema, dict):
        raise ValidationError("variables_schema must be an object.")
    optional = schema.get("optional", [])
    if optional and not isinstance(optional, list):
        raise ValidationError("variables_schema.optional must be a list.")
