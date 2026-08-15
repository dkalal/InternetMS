from __future__ import annotations

import hashlib
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from custom_fields.services import CustomFieldService
from billing.models import BillingDocument

from .models import Customer, CustomerDocument, CustomerSite, InternetCustomer, InternetService
from services.models import Package
from users.permissions import PermissionCode, has_tenant_permission


class CustomerServiceError(Exception):
    code = "customer_service_error"


class InternetServiceDomainService:
    """Tenant-safe commands for installed services and their commercial history."""

    @classmethod
    def _require_update_permission(cls, *, organization, actor):
        if not has_tenant_permission(actor, organization, PermissionCode.CUSTOMERS_UPDATE):
            raise PermissionDenied("Insufficient permission to update Internet services.")

    @classmethod
    def _audit(cls, *, organization, actor, action, service, old_value=None, new_value=None, metadata=None):
        AuditLog.objects.create(
            organization=organization,
            tenant=organization,
            actor=actor,
            performed_by=actor,
            action=action,
            action_type=action,
            object_type="InternetService",
            object_id=str(service.id),
            old_value=old_value or {},
            new_value=new_value or {},
            metadata=metadata or {},
        )

    @classmethod
    def add_customer_site(cls, *, organization, actor, site_instance: CustomerSite) -> CustomerSite:
        cls._require_update_permission(organization=organization, actor=actor)
        return CustomerService.upsert_site(
            organization=organization,
            actor=actor,
            site_instance=site_instance,
            packages=None,
        )

    @classmethod
    def add_internet_service(
        cls, *, organization, actor, customer_id: int, site_id: int,
        service_code: str, name: str, ip_address=None, vlan_id=None,
        installed_at=None, technical_notes="",
    ) -> InternetService:
        cls._require_update_permission(organization=organization, actor=actor)
        service_code = (service_code or "").strip()
        if not service_code:
            raise CustomerServiceError("Service code is required.")
        with transaction.atomic():
            customer = Customer.all_objects.select_for_update().filter(
                tenant=organization, id=customer_id, is_deleted=False,
            ).first()
            site = CustomerSite.objects.select_for_update().filter(
                tenant=organization, customer_id=customer_id, id=site_id,
            ).first()
            if customer is None or site is None:
                raise CustomerServiceError("Customer site not found.")
            if not customer.is_internet_customer:
                raise CustomerServiceError("Internet services can only be added to Internet customers.")
            if InternetService.objects.filter(tenant=organization, service_code=service_code).exists():
                raise CustomerServiceError("Service code already exists in this tenant.")
            service = InternetService.objects.create(
                organization=organization, tenant=organization, customer=customer, site=site,
                service_code=service_code, name=(name or "Primary Internet Service").strip(),
                ip_address=ip_address or None, vlan_id=vlan_id or None,
                installed_at=installed_at, technical_notes=(technical_notes or "").strip(),
            )
            cls._audit(
                organization=organization, actor=actor, action="internet_service.created", service=service,
                new_value={"status": service.operational_status}, metadata={"customer_id": customer.id, "site_id": site.id},
            )
            return service

    @classmethod
    def assign_initial_subscription(
        cls, *, organization, actor, service_id: int, package_id: int,
        start_date: date, promotion=None,
    ):
        from billing.services import SubscriptionBillingService

        cls._require_update_permission(organization=organization, actor=actor)
        with transaction.atomic():
            service = InternetService.objects.unscoped().select_for_update().select_related(
                "customer", "site"
            ).filter(tenant=organization, id=service_id).first()
            package = Package.objects.unscoped().filter(tenant=organization, id=package_id, is_active=True).first()
            if service is None or package is None:
                raise CustomerServiceError("Internet service or assignable package not found.")
            subscription = SubscriptionBillingService.get_or_create_subscription(
                organization=organization, customer=service.customer, site=service.site,
                internet_service=service, package=package, start_date=start_date, promotion=promotion,
            )
            service.site.packages.add(package)
            if service.site.is_primary:
                service.customer.packages.add(package)
            cls._audit(
                organization=organization, actor=actor, action="internet_service.subscription_assigned", service=service,
                new_value={"subscription_id": subscription.id, "package_id": package.id},
            )
            return subscription

    @classmethod
    def change_service_package(
        cls, *, organization, actor, service_id: int, package_id: int,
        effective_date: date, reason: str,
    ):
        from billing.models import CustomerSubscription

        cls._require_update_permission(organization=organization, actor=actor)
        reason = (reason or "").strip()
        if not reason:
            raise CustomerServiceError("A reason is required to change package.")
        with transaction.atomic():
            service = InternetService.objects.unscoped().select_for_update().select_related(
                "customer", "site"
            ).filter(tenant=organization, id=service_id).first()
            package = Package.objects.unscoped().filter(tenant=organization, id=package_id, is_active=True).first()
            if service is None or package is None:
                raise CustomerServiceError("Internet service or assignable package not found.")
            current = CustomerSubscription.objects.unscoped().select_for_update().filter(
                tenant=organization, internet_service=service,
                status=CustomerSubscription.Status.ACTIVE,
            ).first()
            if current is None:
                return cls.assign_initial_subscription(
                    organization=organization, actor=actor, service_id=service.id,
                    package_id=package.id, start_date=effective_date,
                )
            if current.package_id == package.id:
                return current
            if effective_date <= current.start_date:
                raise CustomerServiceError("Package change date must be after the current subscription start date.")
            old_package_id = current.package_id
            CustomerSubscription.objects.filter(pk=current.pk).update(
                status=CustomerSubscription.Status.CANCELLED,
                end_date=effective_date - timedelta(days=1),
            )
            replacement = CustomerSubscription.objects.create(
                organization=organization, tenant=organization, customer=service.customer,
                site=service.site, internet_service=service, package=package,
                status=CustomerSubscription.Status.ACTIVE, start_date=effective_date,
                billing_day=current.billing_day, monthly_fee_at_signup=package.monthly_fee,
            )
            service.site.packages.add(package)
            if service.site.is_primary:
                service.customer.packages.add(package)
            if not CustomerSubscription.objects.filter(
                tenant=organization, site=service.site, package_id=old_package_id,
                status=CustomerSubscription.Status.ACTIVE,
            ).exists():
                service.site.packages.remove(old_package_id)
                if service.site.is_primary:
                    service.customer.packages.remove(old_package_id)
            cls._audit(
                organization=organization, actor=actor, action="internet_service.package_changed", service=service,
                old_value={"subscription_id": current.id, "package_id": old_package_id},
                new_value={"subscription_id": replacement.id, "package_id": package.id},
                metadata={"effective_date": effective_date.isoformat(), "reason": reason},
            )
            return replacement

    @classmethod
    def _set_operational_status(cls, *, organization, actor, service_id, status, reason):
        cls._require_update_permission(organization=organization, actor=actor)
        reason = (reason or "").strip()
        if not reason:
            raise CustomerServiceError("A reason is required for a service status change.")
        with transaction.atomic():
            service = InternetService.objects.unscoped().select_for_update().filter(
                tenant=organization, id=service_id,
            ).first()
            if service is None:
                raise CustomerServiceError("Internet service not found.")
            old = service.operational_status
            if old == status:
                return service
            if old == InternetService.OperationalStatus.DISCONNECTED:
                raise CustomerServiceError("A disconnected service cannot change status implicitly.")
            if status == InternetService.OperationalStatus.ACTIVE and old not in {
                InternetService.OperationalStatus.UNKNOWN,
                InternetService.OperationalStatus.BLOCKED,
            }:
                raise CustomerServiceError("Only an unverified or blocked service can be activated.")
            service.operational_status = status
            service.disconnected_at = timezone.now() if status == InternetService.OperationalStatus.DISCONNECTED else None
            service.save(update_fields=["operational_status", "disconnected_at", "updated_at"])
            cls._audit(
                organization=organization, actor=actor, action="internet_service.status_changed", service=service,
                old_value={"status": old}, new_value={"status": status}, metadata={"reason": reason},
            )
            return service

    @classmethod
    def block_service(cls, **kwargs):
        return cls._set_operational_status(status=InternetService.OperationalStatus.BLOCKED, **kwargs)

    @classmethod
    def unblock_service(cls, **kwargs):
        return cls._set_operational_status(status=InternetService.OperationalStatus.ACTIVE, **kwargs)

    @classmethod
    def disconnect_service(cls, **kwargs):
        return cls._set_operational_status(status=InternetService.OperationalStatus.DISCONNECTED, **kwargs)


class CustomerService:
    @classmethod
    def _primary_site_defaults(cls, customer: Customer) -> dict:
        return {
            "name": "Main Office",
            "location": customer.location,
            "address": customer.address,
            "ip_address": customer.ip_address,
            "vlan_id": customer.vlan_id,
            "is_primary": True,
        }

    @classmethod
    def ensure_primary_site(cls, *, organization, customer: Customer) -> CustomerSite:
        site = customer.sites.filter(is_primary=True).order_by("id").first()
        if site is not None:
            CustomerSite.objects.filter(customer=customer).exclude(pk=site.pk).update(is_primary=False)
            return site

        site = customer.sites.order_by("id").first()
        if site is not None:
            CustomerSite.objects.filter(customer=customer).exclude(pk=site.pk).update(is_primary=False)
            site.is_primary = True
            site.organization = organization
            site.tenant = organization
            site.save(update_fields=["is_primary", "organization", "tenant"])
            return site

        return CustomerSite.objects.create(
            organization=organization,
            tenant=organization,
            customer=customer,
            **cls._primary_site_defaults(customer),
        )

    @classmethod
    def create_customer_with_primary_service(
        cls, *, organization, actor, customer_instance, packages, customer_type,
        internet_profile_instance, status_change_reason="", custom_field_data=None,
    ) -> Customer:
        """Create an account and its explicit primary connection topology atomically."""
        with transaction.atomic():
            package_rows = list(packages or [])
            customer = cls.upsert_customer(
                organization=organization,
                actor=actor,
                customer_instance=customer_instance,
                packages=None,
                customer_type=customer_type,
                existing_internet_profile=None,
                internet_profile_instance=internet_profile_instance,
                status_change_reason=status_change_reason,
                custom_field_data=custom_field_data,
            )
            if customer_type != "internet":
                return customer

            site = cls.ensure_primary_site(organization=organization, customer=customer)
            service_count = max(1, len(package_rows))
            for index in range(service_count):
                package = package_rows[index] if index < len(package_rows) else None
                service = InternetServiceDomainService.add_internet_service(
                    organization=organization,
                    actor=actor,
                    customer_id=customer.id,
                    site_id=site.id,
                    service_code=f"CUST-{customer.id}-SVC-{index + 1:02d}",
                    name="Primary Internet Service" if index == 0 else f"Internet Service {index + 1}",
                    ip_address=customer.ip_address if index == 0 else None,
                    vlan_id=customer.vlan_id if index == 0 else None,
                )
                if package is not None:
                    start_date = (
                        getattr(internet_profile_instance, "start_date", None)
                        or timezone.localdate()
                    )
                    InternetServiceDomainService.assign_initial_subscription(
                        organization=organization,
                        actor=actor,
                        service_id=service.id,
                        package_id=package.id,
                        start_date=start_date,
                    )
            return customer

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
        custom_field_data=None,
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
            primary_site = None
            if customer_type == "internet":
                primary_site = cls.ensure_primary_site(organization=organization, customer=customer_instance)
            else:
                primary_site = customer_instance.primary_site
            if packages is not None:
                if customer_instance.status == Customer.Status.SUSPENDED and packages:
                    raise CustomerServiceError(
                        "Packages cannot be assigned to a suspended customer. Reactivate the customer first."
                    )
                customer_instance.packages.set(packages)
                if primary_site is not None:
                    primary_site.packages.set(packages)
                if customer_instance.customer_type == "internet":
                    from billing.services import SubscriptionBillingService

                    SubscriptionBillingService.sync_customer_package_subscriptions(
                        organization=organization,
                        customer=customer_instance,
                        site=primary_site,
                        packages=packages,
                    )

            if customer_type == "internet":
                if internet_profile_instance is None:
                    raise CustomerServiceError("Internet profile data is required for internet customers.")
                internet_profile_instance.customer = customer_instance
                internet_profile_instance.save()
            else:
                if existing_internet_profile is not None:
                    existing_internet_profile.delete()

            if custom_field_data is not None:
                CustomFieldService.save_custom_field_values(customer_instance, custom_field_data, user=actor)

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
    def upsert_site(
        cls,
        *,
        organization,
        actor,
        site_instance: CustomerSite,
        packages,
        status_change_reason: str = "",
    ) -> CustomerSite:
        with transaction.atomic():
            customer = (
                Customer.all_objects.select_for_update()
                .filter(organization=organization, pk=site_instance.customer_id)
                .first()
            )
            if customer is None:
                raise CustomerServiceError("Customer not found.")
            if customer.is_deleted:
                raise CustomerServiceError("Archived customer cannot be updated. Restore first.")

            site_instance.organization = organization
            site_instance.tenant = organization
            site_instance.customer = customer
            if site_instance.is_primary:
                CustomerSite.objects.filter(customer=customer).exclude(pk=site_instance.pk).update(is_primary=False)
            site_instance.save()
            if packages is not None:
                site_instance.packages.set(packages)
                if site_instance.is_primary:
                    customer.packages.set(packages)
                from billing.services import SubscriptionBillingService

                SubscriptionBillingService.sync_customer_site_package_subscriptions(
                    organization=organization,
                    customer=customer,
                    site=site_instance,
                    packages=packages,
                )

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=actor,
                action="customer.site_upserted",
                object_type="CustomerSite",
                object_id=str(site_instance.id),
                metadata={
                    "customer_id": customer.id,
                    "site_name": site_instance.name,
                    "is_primary": site_instance.is_primary,
                    "status_change_reason": status_change_reason,
                },
            )
            return site_instance

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
