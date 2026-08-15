"""Safe, tenant-scoped numbering for inventory purchasing records."""

from __future__ import annotations

from datetime import date

from django.db import IntegrityError, transaction

from users.models import Organization

from .models import Purchase, PurchaseReferenceSequence


class PurchaseReferenceNumberService:
    """Preview and atomically allocate human-readable purchase references."""

    PREFIX = "PUR"

    @classmethod
    def preview_next_number(cls, *, organization: Organization, purchase_date: date | None = None) -> str:
        """Return the current likely next reference without reserving it.

        Opening, refreshing, or abandoning a form therefore never advances
        the official sequence. Allocation happens only inside the successful
        purchase-save transaction below.
        """
        sequence_date = purchase_date or date.today()
        counter = PurchaseReferenceSequence.objects.unscoped().filter(tenant=organization).first()
        next_number = (counter.last_number if counter else 0) + 1
        while True:
            candidate = cls._format_number(sequence_date, next_number)
            if not Purchase.objects.unscoped().filter(
                tenant=organization, reference_number__iexact=candidate
            ).exists():
                return candidate
            next_number += 1

    @classmethod
    def next_number(cls, *, organization: Organization, purchase_date: date | None = None) -> str:
        """Allocate the next reference once a purchase is successfully saved."""
        sequence_date = purchase_date or date.today()
        with transaction.atomic():
            counter = cls._get_or_create_locked_counter(organization=organization)
            while True:
                counter.last_number += 1
                candidate = cls._format_number(sequence_date, counter.last_number)
                # Existing manually supplied references may already use this
                # format. Do not issue a duplicate when adopting numbering.
                if not Purchase.objects.unscoped().filter(
                    tenant=organization, reference_number__iexact=candidate
                ).exists():
                    counter.save(update_fields=["last_number", "updated_at"])
                    return candidate

    @classmethod
    def _format_number(cls, purchase_date: date, sequence_number: int) -> str:
        return f"{cls.PREFIX}-{purchase_date:%Y}-{sequence_number:05d}"

    @staticmethod
    def _get_or_create_locked_counter(*, organization: Organization) -> PurchaseReferenceSequence:
        lookup = {"organization": organization, "tenant": organization}
        try:
            return PurchaseReferenceSequence.objects.select_for_update().get(**lookup)
        except PurchaseReferenceSequence.DoesNotExist:
            try:
                # Keep a concurrent create race inside a savepoint so the
                # enclosing transaction remains usable for the locked retry.
                with transaction.atomic():
                    return PurchaseReferenceSequence.objects.create(last_number=0, **lookup)
            except IntegrityError:
                return PurchaseReferenceSequence.objects.select_for_update().get(**lookup)
