from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from billing.models import BillingDocument

from .models import Customer, CustomerDocument, InternetCustomer


class CustomerServiceError(Exception):
    code = "customer_service_error"


class CustomerService:
    @classmethod
    def upsert_customer(
        cls,
        *,
        organization,
        actor,
        customer_instance: Customer,
        packages,
        customer_type: str,
        existing_internet_profile: InternetCustomer | None,
        internet_profile_instance: InternetCustomer | None,
        status_change_reason: str = "",
    ) -> Customer:
        """
        Create/update customer + related subscription profile with tenant isolation.
        """

        with transaction.atomic():
            previous_status = None
            if customer_instance.pk:
                existing = (
                    Customer.all_objects.select_for_update()
                    .filter(organization=organization, pk=customer_instance.pk)
                    .only("id", "status", "is_deleted")
                    .first()
                )
                if existing is None:
                    raise CustomerServiceError("Customer not found.")
                if existing.is_deleted:
                    raise CustomerServiceError("Archived customer cannot be updated. Restore first.")
                previous_status = existing.status

            customer_instance.organization = organization
            customer_instance.tenant = organization
            customer_instance.save()
            if packages is not None:
                customer_instance.packages.set(packages)
                if customer_instance.customer_type == "internet":
                    from billing.services import SubscriptionBillingService

                    SubscriptionBillingService.sync_customer_package_subscriptions(
                        organization=organization,
                        customer=customer_instance,
                    )

            if customer_type == "internet":
                if internet_profile_instance is None:
                    raise CustomerServiceError("Internet profile data is required for internet customers.")
                internet_profile_instance.customer = customer_instance
                internet_profile_instance.save()
            else:
                if existing_internet_profile is not None:
                    existing_internet_profile.delete()

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=actor,
                action="customer.upserted",
                object_type="Customer",
                object_id=str(customer_instance.id),
                metadata={"customer_type": customer_type, "status": customer_instance.status},
            )

            if previous_status and previous_status != customer_instance.status:
                AuditLog.objects.create(
                    organization=organization,
                    tenant=organization,
                    actor=actor,
                    action="customer.status_changed",
                    object_type="Customer",
                    object_id=str(customer_instance.id),
                    metadata={
                        "from": previous_status,
                        "to": customer_instance.status,
                        "reason": status_change_reason,
                    },
                )
                if previous_status == Customer.Status.ACTIVE and customer_instance.status != Customer.Status.ACTIVE:
                    AuditLog.objects.create(
                        organization=organization,
                        tenant=organization,
                        actor=actor,
                        action="customer.deactivated",
                        object_type="Customer",
                        object_id=str(customer_instance.id),
                        metadata={"to": customer_instance.status, "reason": status_change_reason},
                    )

            return customer_instance

    @classmethod
    def soft_delete_customer(cls, *, organization, actor, customer_id: int, reason: str = "") -> None:
        with transaction.atomic():
            customer = (
                Customer.all_objects.select_for_update()
                .filter(organization=organization, id=customer_id)
                .first()
            )
            if customer is None:
                raise CustomerServiceError("Customer not found.")

            if customer.is_deleted:
                return

            customer.is_deleted = True
            customer.deleted_at = timezone.now()
            customer.deleted_by = actor
            customer.status = Customer.Status.INACTIVE
            customer.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "status"])

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=actor,
                action="customer.soft_deleted",
                object_type="Customer",
                object_id=str(customer.id),
                metadata={"reason": reason},
            )

    @classmethod
    def restore_customer(cls, *, organization, actor, customer_id: int) -> None:
        with transaction.atomic():
            customer = (
                Customer.all_objects.select_for_update()
                .filter(organization=organization, id=customer_id)
                .first()
            )
            if customer is None:
                raise CustomerServiceError("Customer not found.")

            if not customer.is_deleted:
                return

            customer.is_deleted = False
            customer.deleted_at = None
            customer.deleted_by = None
            customer.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=actor,
                action="customer.restored",
                object_type="Customer",
                object_id=str(customer.id),
                metadata={},
            )

    @classmethod
    def set_status(cls, *, organization, actor, customer_id: int, status: str, reason: str = "") -> None:
        if status not in {Customer.Status.ACTIVE, Customer.Status.INACTIVE, Customer.Status.SUSPENDED}:
            raise CustomerServiceError("Invalid status.")

        with transaction.atomic():
            customer = (
                Customer.all_objects.select_for_update()
                .filter(organization=organization, id=customer_id)
                .first()
            )
            if customer is None:
                raise CustomerServiceError("Customer not found.")

            old = customer.status
            if old == status:
                return
            customer.status = status
            customer.save(update_fields=["status"])

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=actor,
                action="customer.status_changed",
                object_type="Customer",
                object_id=str(customer.id),
                metadata={"from": old, "to": status, "reason": reason},
            )

    @classmethod
    def anonymize_customer(cls, *, organization, actor, customer_id: int) -> None:
        with transaction.atomic():
            customer = (
                Customer.all_objects.select_for_update()
                .filter(organization=organization, id=customer_id)
                .first()
            )
            if customer is None:
                raise CustomerServiceError("Customer not found.")

            token_src = f"{settings.SECRET_KEY}:{organization.id}:{customer.id}:{timezone.now().isoformat()}"
            token = hashlib.sha256(token_src.encode("utf-8")).hexdigest()[:12]
            customer.name = f"Anonymized-{customer.id}-{token}"
            customer.email = None
            customer.phone = None
            customer.address = None
            customer.save(update_fields=["name", "email", "phone", "address"])

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=actor,
                action="customer.anonymized",
                object_type="Customer",
                object_id=str(customer.id),
                metadata={},
            )

    @classmethod
    def hard_delete_customer(
        cls,
        *,
        organization,
        actor,
        customer_id: int,
        confirm_phrase: str,
        confirm_one: bool,
        confirm_two: bool,
    ) -> None:
        if not getattr(actor, "is_superuser", False):
            raise PermissionDenied("Super admin required.")

        expected = f"DELETE {customer_id}"
        if confirm_phrase.strip() != expected or not confirm_one or not confirm_two:
            raise CustomerServiceError("Hard delete confirmation failed.")

        blocked_reason = None
        customer_obj_id = str(customer_id)

        with transaction.atomic():
            customer = (
                Customer.all_objects.select_for_update()
                .filter(organization=organization, id=customer_id)
                .first()
            )
            if customer is None:
                blocked_reason = "Customer not found."
            else:
                customer_obj_id = str(customer.id)
                if BillingDocument.objects.filter(organization=organization, customer_id=customer.id).exists():
                    blocked_reason = "Customer has billing documents."
                elif CustomerDocument.objects.filter(organization=organization, customer_id=customer.id).exists():
                    blocked_reason = "Customer has uploaded documents."
                elif InternetCustomer.objects.filter(customer=customer).exists():
                    blocked_reason = "Customer has an active subscription profile."
                elif AuditLog.objects.filter(organization=organization, object_type="Customer", object_id=str(customer.id)).exists():
                    blocked_reason = "Audit logs reference this customer."

                if blocked_reason is None:
                    AuditLog.objects.create(
                        organization=organization,
                        tenant=organization,
                        actor=actor,
                        action="customer.hard_delete.attempt",
                        object_type="Customer",
                        object_id=str(customer.id),
                        metadata={"allowed": True},
                    )
                    AuditLog.objects.create(
                        organization=organization,
                        tenant=organization,
                        actor=actor,
                        action="customer.hard_deleted",
                        object_type="Customer",
                        object_id=str(customer.id),
                        metadata={},
                    )
                    customer.delete()
                    return

        AuditLog.objects.create(
            organization=organization,
            tenant=organization,
            actor=actor,
            action="customer.hard_delete.attempt",
            object_type="Customer",
            object_id=customer_obj_id,
            metadata={"allowed": False, "blocked_reason": blocked_reason},
        )
        raise CustomerServiceError(blocked_reason or "Hard delete not allowed.")
