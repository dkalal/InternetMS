from django.test import TestCase
from django.urls import reverse

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from audit.models import AuditLog
from billing.models import BillingDocument
from billing.services import BillingService, BillingServiceError, LineItemInput
from customers.models import Customer
from customers.forms import CustomerForm
from customers.services import CustomerService, CustomerServiceError
from products.models import Product
from users.models import Organization, UserAccessProfile


User = get_user_model()


class CustomerDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="pass")
        self.superuser = User.objects.create_superuser(username="root", password="pass")
        self.org1 = Organization.objects.create(name="Org One", slug="org-one")
        self.org2 = Organization.objects.create(name="Org Two", slug="org-two")

        self.customer1 = Customer.all_objects.create(
            organization=self.org1,
            name="Alice",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        self.customer2 = Customer.all_objects.create(
            organization=self.org2,
            name="Bob",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )

        self.product = Product.objects.create(
            organization=self.org1,
            name="Router",
            category="hardware",
            quantity=Decimal("1.00"),
            measure_unit="Unit",
            buying_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            stock=1,
            is_active=True,
        )

    def test_soft_delete_filters_default_queries(self):
        CustomerService.soft_delete_customer(
            organization=self.org1,
            actor=self.user,
            customer_id=self.customer1.id,
        )
        self.assertFalse(Customer.objects.filter(id=self.customer1.id).exists())
        deleted = Customer.all_objects.get(id=self.customer1.id)
        self.assertTrue(deleted.is_deleted)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(deleted.deleted_by_id, self.user.id)

        CustomerService.restore_customer(
            organization=self.org1,
            actor=self.user,
            customer_id=self.customer1.id,
        )
        self.assertTrue(Customer.objects.filter(id=self.customer1.id).exists())

    def test_tenant_isolation_on_soft_delete(self):
        with self.assertRaises(CustomerServiceError):
            CustomerService.soft_delete_customer(
                organization=self.org1,
                actor=self.user,
                customer_id=self.customer2.id,
            )

    def test_hard_delete_blocked_if_billing_exists(self):
        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer1.id,
            issue_date=timezone.now().date(),
            items=[LineItemInput(product_id=self.product.id, quantity=Decimal("1.00"), unit_price=Decimal("150.00"))],
        )
        self.assertIsNotNone(invoice.id)

        with self.assertRaises(CustomerServiceError):
            CustomerService.hard_delete_customer(
                organization=self.org1,
                actor=self.superuser,
                customer_id=self.customer1.id,
                confirm_phrase=f"DELETE {self.customer1.id}",
                confirm_one=True,
                confirm_two=True,
            )

        self.assertTrue(Customer.all_objects.filter(id=self.customer1.id).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.org1,
                action="customer.hard_delete.attempt",
                object_type="Customer",
                object_id=str(self.customer1.id),
            ).exists()
        )

    def test_anonymization_preserves_financial_history(self):
        BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer1.id,
            issue_date=timezone.now().date(),
            items=[LineItemInput(product_id=self.product.id, quantity=Decimal("1.00"), unit_price=Decimal("150.00"))],
        )

        CustomerService.anonymize_customer(
            organization=self.org1,
            actor=self.user,
            customer_id=self.customer1.id,
        )

        customer = Customer.all_objects.get(id=self.customer1.id)
        self.assertIsNone(customer.email)
        self.assertIsNone(customer.phone)
        self.assertTrue(customer.name.startswith("Anonymized-"))
        self.assertTrue(BillingDocument.objects.filter(organization=self.org1, customer_id=self.customer1.id).exists())
        self.assertTrue(AuditLog.objects.filter(organization=self.org1, action="customer.anonymized").exists())

    def test_billing_blocked_if_customer_not_active(self):
        CustomerService.set_status(
            organization=self.org1,
            actor=self.user,
            customer_id=self.customer1.id,
            status=Customer.Status.INACTIVE,
        )

        with self.assertRaises(BillingServiceError):
            BillingService.create_document(
                organization=self.org1,
                created_by=self.user,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=self.customer1.id,
                issue_date=timezone.now().date(),
                items=[LineItemInput(product_id=self.product.id, quantity=Decimal("1.00"), unit_price=Decimal("150.00"))],
            )

    def test_customer_form_requires_reason_when_status_changes(self):
        form = CustomerForm(
            data={
                "name": self.customer1.name,
                "customer_type": self.customer1.customer_type,
                "status": Customer.Status.SUSPENDED,
                "location": self.customer1.location,
            },
            instance=self.customer1,
            organization=self.org1,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("status_change_reason", form.errors)

    def test_status_change_reason_is_audited(self):
        customer = Customer.all_objects.get(id=self.customer1.id)
        customer.customer_type = "random"
        customer.status = Customer.Status.SUSPENDED
        CustomerService.upsert_customer(
            organization=self.org1,
            actor=self.user,
            customer_instance=customer,
            packages=None,
            customer_type="random",
            existing_internet_profile=None,
            internet_profile_instance=None,
            status_change_reason="Payment overdue",
        )

        log = AuditLog.objects.filter(
            organization=self.org1,
            action="customer.status_changed",
            object_type="Customer",
            object_id=str(self.customer1.id),
        ).latest("id")
        self.assertEqual(log.metadata["reason"], "Payment overdue")


class CustomerRBACIsolationTests(TestCase):
    def setUp(self):
        self.org1 = Organization.objects.create(name="Tenant A", slug="tenant-a")
        self.org2 = Organization.objects.create(name="Tenant B", slug="tenant-b")
        self.customer_a = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Alice A",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        self.customer_b = Customer.all_objects.create(
            organization=self.org2,
            tenant=self.org2,
            name="Bob B",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Arusha",
        )

        self.staff = User.objects.create_user(username="staff", password="pass")
        self.admin = User.objects.create_user(username="admin", password="pass")
        self.super_admin = User.objects.create_superuser(username="super", password="pass")

        UserAccessProfile.objects.create(
            user=self.staff,
            tenant=self.org1,
            role=UserAccessProfile.Role.TENANT_STAFF,
        )
        UserAccessProfile.objects.create(
            user=self.admin,
            tenant=self.org1,
            role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        UserAccessProfile.objects.create(
            user=self.super_admin,
            tenant=None,
            role=UserAccessProfile.Role.SUPER_ADMIN,
        )

    def test_tenant_a_user_cannot_see_tenant_b_customer(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(reverse("customer-list"))
        self.assertEqual(response.status_code, 200)
        customers = list(response.context["customers"])
        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0].id, self.customer_a.id)

    def test_staff_cannot_archive_customer(self):
        self.client.login(username="staff", password="pass")
        response = self.client.post(reverse("customer-delete", args=[self.customer_a.id]))
        self.assertEqual(response.status_code, 403)
        self.customer_a = Customer.all_objects.get(id=self.customer_a.id)
        self.assertFalse(self.customer_a.is_deleted)

    def test_admin_can_archive_customer(self):
        self.client.login(username="admin", password="pass")
        response = self.client.post(reverse("customer-delete", args=[self.customer_a.id]))
        self.assertEqual(response.status_code, 302)
        self.customer_a = Customer.all_objects.get(id=self.customer_a.id)
        self.assertTrue(self.customer_a.is_deleted)

    def test_super_admin_without_tenant_context_cannot_access_tenant_operations(self):
        self.client.login(username="super", password="pass")
        response = self.client.get(reverse("customer-list"))
        self.assertEqual(response.status_code, 403)
