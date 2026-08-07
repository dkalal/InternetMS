from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from django.db import IntegrityError, transaction

from users.models import Organization

from .models import BillingDocument, DocumentSequence


@dataclass(frozen=True)
class GeneratedDocumentNumber:
    value: str
    sequence_number: int
    tenant_code: str
    sequence_date: date


class DocumentNumberService:
    PREFIX = {
        BillingDocument.DocumentType.QUOTATION: "QTN",
        BillingDocument.DocumentType.INVOICE: "INV",
        BillingDocument.DocumentType.RECEIPT: "RCT",
        BillingDocument.DocumentType.CREDIT_NOTE: "CRN",
    }
    ANNUAL_DOCUMENT_TYPES = {
        BillingDocument.DocumentType.QUOTATION,
        BillingDocument.DocumentType.INVOICE,
        BillingDocument.DocumentType.RECEIPT,
    }

    @classmethod
    def next_number(
        cls,
        *,
        organization: Organization,
        document_type: str,
        issue_date: date,
    ) -> GeneratedDocumentNumber:
        with transaction.atomic():
            counter = cls._get_or_create_locked_counter(
                organization=organization,
                document_type=document_type,
                issue_date=issue_date,
            )
            counter.last_number += 1
            counter.save(update_fields=["last_number"])

        tenant_code = "JS" if document_type in cls.ANNUAL_DOCUMENT_TYPES else cls.get_tenant_code(organization)
        return GeneratedDocumentNumber(
            value=cls._format_number(
                document_type=document_type,
                tenant_code=tenant_code,
                issue_date=issue_date,
                sequence_number=counter.last_number,
            ),
            sequence_number=counter.last_number,
            tenant_code=tenant_code,
            sequence_date=issue_date,
        )

    @classmethod
    def get_tenant_code(cls, organization: Organization) -> str:
        for attr in ("serial_prefix", "document_prefix", "tenant_code", "code", "short_code"):
            raw = getattr(organization, attr, "")
            if raw:
                normalized = cls._normalize_code(raw)
                if normalized:
                    return normalized

        slug = getattr(organization, "slug", "") or ""
        slug_parts = [part for part in re.split(r"[^A-Za-z0-9]+", slug) if part]
        if slug_parts and len(slug_parts[0]) <= 6:
            normalized = cls._normalize_code(slug_parts[0])
            if normalized:
                return normalized

        name = getattr(organization, "name", "") or slug
        name_parts = [part for part in re.split(r"[^A-Za-z0-9]+", name) if part]
        if len(name_parts) >= 2:
            return "".join(part[0].upper() for part in name_parts[:4])

        return cls._normalize_code(name or "TENANT")

    @classmethod
    def _normalize_code(cls, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "", value or "").upper()
        return (cleaned[:8] or "TENANT")

    @classmethod
    def _format_number(cls, *, document_type: str, tenant_code: str, issue_date: date, sequence_number: int) -> str:
        prefix = cls.PREFIX[document_type]
        if document_type in cls.ANNUAL_DOCUMENT_TYPES:
            return f"{prefix}-JS-{issue_date:%Y}-{sequence_number:04d}"
        return f"{prefix}-{tenant_code}-{issue_date:%Y%m%d}-{sequence_number:04d}"

    @classmethod
    def _get_or_create_locked_counter(
        cls,
        *,
        organization: Organization,
        document_type: str,
        issue_date: date,
    ) -> DocumentSequence:
        lookup = {
            "organization": organization,
            "tenant": organization,
            "document_type": document_type,
        }
        if document_type in cls.ANNUAL_DOCUMENT_TYPES:
            lookup.update(year=issue_date.year, sequence_date=None)
        else:
            # Credit-note numbering is outside the annual-numbering change and
            # intentionally retains its existing daily allocation behavior.
            lookup.update(year=None, sequence_date=issue_date)
        try:
            return DocumentSequence.objects.select_for_update().get(**lookup)
        except DocumentSequence.DoesNotExist:
            try:
                # Isolate a concurrent insert conflict in a savepoint so the
                # surrounding transaction remains usable before we lock/read it.
                with transaction.atomic():
                    return DocumentSequence.objects.create(last_number=0, **lookup)
            except IntegrityError:
                return DocumentSequence.objects.select_for_update().get(**lookup)


def generate_document_number(
    tenant: Organization,
    document_type: str,
    document_date: date,
) -> str:
    """Allocate the next official number for a tenant and business date."""
    return DocumentNumberService.next_number(
        organization=tenant,
        document_type=document_type,
        issue_date=document_date,
    ).value
