from django.conf import settings
from django.db import migrations, models


DEFAULT_TENANT_SLUG = "default-tenant"


def _get_default_tenant(apps):
    Organization = apps.get_model("users", "Organization")
    tenant, _ = Organization.objects.get_or_create(
        slug=DEFAULT_TENANT_SLUG,
        defaults={"name": "Default Tenant", "is_active": True},
    )
    return tenant


def forwards(apps, schema_editor):
    Organization = apps.get_model("users", "Organization")
    Membership = apps.get_model("users", "Membership")
    UserAccessProfile = apps.get_model("users", "UserAccessProfile")
    Customer = apps.get_model("customers", "Customer")
    CustomerDocument = apps.get_model("customers", "CustomerDocument")
    InternetCustomer = apps.get_model("customers", "InternetCustomer")
    Product = apps.get_model("products", "Product")
    Package = apps.get_model("services", "Package")
    BillingDocument = apps.get_model("billing", "BillingDocument")
    BillingLineItem = apps.get_model("billing", "BillingLineItem")
    DocumentSequence = apps.get_model("billing", "DocumentSequence")
    AuditLog = apps.get_model("audit", "AuditLog")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))

    default_tenant = _get_default_tenant(apps)

    for model in (Customer, CustomerDocument, Product, Package, BillingDocument, BillingLineItem, DocumentSequence, AuditLog):
        model.objects.filter(tenant_id__isnull=True, organization_id__isnull=False).update(tenant_id=models.F("organization_id"))
        model.objects.filter(organization_id__isnull=True).update(organization_id=default_tenant.id)
        model.objects.filter(tenant_id__isnull=True).update(tenant_id=default_tenant.id)

    for internet_customer in InternetCustomer.objects.filter(tenant_id__isnull=True).select_related("customer"):
        internet_customer.tenant_id = internet_customer.customer.organization_id or default_tenant.id
        internet_customer.save(update_fields=["tenant"])
    InternetCustomer.objects.filter(tenant_id__isnull=True).update(tenant_id=default_tenant.id)

    for user in User.objects.all().only("id", "is_superuser"):
        if user.is_superuser:
            UserAccessProfile.objects.get_or_create(
                user_id=user.id,
                defaults={"role": "SUPER_ADMIN", "tenant_id": None},
            )
            continue

        membership = (
            Membership.objects.filter(user_id=user.id, is_active=True, organization__is_active=True)
            .order_by("organization_id")
            .first()
        )
        tenant_id = membership.organization_id if membership else default_tenant.id
        role = "TENANT_ADMIN" if membership and membership.role in {"owner", "admin"} else "TENANT_STAFF"
        UserAccessProfile.objects.get_or_create(
            user_id=user.id,
            defaults={"role": role, "tenant_id": tenant_id},
        )

    if not Membership.objects.filter(organization_id=default_tenant.id).exists():
        for user in User.objects.filter(is_superuser=False).only("id"):
            Membership.objects.get_or_create(
                organization_id=default_tenant.id,
                user_id=user.id,
                defaults={"role": "member", "is_active": True},
            )


def backwards(apps, schema_editor):
    Organization = apps.get_model("users", "Organization")
    UserAccessProfile = apps.get_model("users", "UserAccessProfile")
    Customer = apps.get_model("customers", "Customer")
    CustomerDocument = apps.get_model("customers", "CustomerDocument")
    InternetCustomer = apps.get_model("customers", "InternetCustomer")
    Product = apps.get_model("products", "Product")
    Package = apps.get_model("services", "Package")
    BillingDocument = apps.get_model("billing", "BillingDocument")
    BillingLineItem = apps.get_model("billing", "BillingLineItem")
    DocumentSequence = apps.get_model("billing", "DocumentSequence")
    AuditLog = apps.get_model("audit", "AuditLog")

    for model in (Customer, CustomerDocument, InternetCustomer, Product, Package, BillingDocument, BillingLineItem, DocumentSequence, AuditLog):
        model.objects.update(tenant_id=None)

    UserAccessProfile.objects.all().delete()
    Organization.objects.filter(slug=DEFAULT_TENANT_SLUG).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0005_tenant_useraccessprofile"),
        ("customers", "0012_customer_customers_c_tenant__d5e64f_idx_and_more"),
        ("products", "0005_product_tenant"),
        ("services", "0006_package_tenant"),
        ("billing", "0004_billingdocument_billing_bil_tenant__133dec_idx_and_more"),
        ("audit", "0003_auditlog_tenant"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
