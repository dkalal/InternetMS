from django.test import TestCase
from django.urls import reverse

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta, date
from unittest.mock import patch

from audit.models import AuditLog
from billing.models import BillingDocument, BillingLineItem, SubscriptionPeriod
from billing.models import CustomerSubscription
from billing.services import BillingService, BillingServiceError, LineItemInput, SubscriptionBillingService
from customers.models import Customer, CustomerSite, InternetCustomer, InternetService
from customers.forms import CustomerForm
from customers.services import CustomerService, CustomerServiceError, InternetServiceDomainService
from integrations.models import ExternalAssetReference
from products.models import Product
from services.models import Package
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

    def test_customer_package_choices_show_speed_and_monthly_price(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Business Fiber",
            package_type="indoor",
            speed="50 Mbps",
            monthly_fee=Decimal("125000.00"),
            setup_fee=Decimal("0.00"),
            description="Business package",
        )

        form = CustomerForm(organization=self.org1)

        label = dict(form.fields["packages"].choices)[package.id]
        self.assertEqual(label, "Business Fiber | 50 Mbps | TZS 125,000/month")

    def test_customer_edit_preserves_assigned_package_selection_and_tenant_scope(self):
        assigned_inactive = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Legacy Fiber",
            package_type="indoor",
            speed="20 Mbps",
            monthly_fee=Decimal("75000.00"),
            setup_fee=Decimal("0.00"),
            description="Existing customer package",
            is_active=False,
        )
        other_tenant = Package.objects.create(
            organization=self.org2,
            tenant=self.org2,
            name="Other Tenant Fiber",
            package_type="outdoor",
            speed="50 Mbps",
            monthly_fee=Decimal("150000.00"),
            setup_fee=Decimal("0.00"),
            description="Must remain isolated",
            is_active=True,
        )
        self.customer1.packages.add(assigned_inactive)

        form = CustomerForm(instance=self.customer1, organization=self.org1)

        package_ids = set(form.fields["packages"].queryset.values_list("pk", flat=True))
        self.assertIn(assigned_inactive.pk, package_ids)
        self.assertNotIn(other_tenant.pk, package_ids)
        self.assertIn(str(assigned_inactive.pk), form.selected_package_ids)

    def test_customer_edit_resolves_legacy_package_from_active_primary_site_subscription(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Subscription Source Fiber",
            package_type="indoor",
            speed="30 Mbps",
            monthly_fee=Decimal("95000.00"),
            setup_fee=Decimal("0.00"),
            description="Stored on subscription only",
            is_active=True,
        )
        primary_site = CustomerService.ensure_primary_site(
            organization=self.org1,
            customer=self.customer1,
        )
        CustomerSubscription.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=self.customer1,
            site=primary_site,
            package=package,
            status=CustomerSubscription.Status.ACTIVE,
            start_date=date(2026, 6, 1),
            monthly_fee_at_signup=package.monthly_fee,
        )
        self.assertFalse(self.customer1.packages.exists())
        self.assertFalse(primary_site.packages.exists())

        form = CustomerForm(instance=self.customer1, organization=self.org1)

        self.assertIn(str(package.pk), form.selected_package_ids)
        self.assertIn(package.pk, form.fields["packages"].queryset.values_list("pk", flat=True))

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

    def test_upsert_customer_creates_primary_site_and_syncs_packages(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Office Fiber",
            package_type="indoor",
            speed="30 Mbps",
            monthly_fee=Decimal("75000.00"),
            setup_fee=Decimal("0.00"),
            description="Primary office package",
        )
        customer = Customer.all_objects.get(id=self.customer1.id)
        customer.customer_type = "internet"
        internet_profile = InternetCustomer(
            package_type="indoor",
            start_date=timezone.now().date(),
        )

        saved = CustomerService.upsert_customer(
            organization=self.org1,
            actor=self.user,
            customer_instance=customer,
            packages=[package],
            customer_type="internet",
            existing_internet_profile=None,
            internet_profile_instance=internet_profile,
        )

        self.assertEqual(saved.sites.count(), 1)
        primary_site = saved.sites.get(is_primary=True)
        self.assertEqual(primary_site.name, "Main Office")
        self.assertTrue(
            saved.subscriptions.filter(package=package, site=primary_site, status=CustomerSubscription.Status.ACTIVE).exists()
        )

    def test_same_package_can_exist_on_multiple_sites(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Shared Business Fiber",
            package_type="indoor",
            speed="100 Mbps",
            monthly_fee=Decimal("150000.00"),
            setup_fee=Decimal("0.00"),
            description="Same package at two offices",
        )
        customer = Customer.all_objects.get(id=self.customer1.id)
        customer.customer_type = "internet"
        internet_profile = InternetCustomer(
            package_type="indoor",
            start_date=timezone.now().date(),
        )
        CustomerService.upsert_customer(
            organization=self.org1,
            actor=self.user,
            customer_instance=customer,
            packages=[package],
            customer_type="internet",
            existing_internet_profile=None,
            internet_profile_instance=internet_profile,
        )
        primary_site = customer.sites.get(is_primary=True)
        second_site = CustomerSite.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=customer,
            name="Branch Office",
            location="Arusha",
            is_primary=False,
        )

        CustomerService.upsert_site(
            organization=self.org1,
            actor=self.user,
            site_instance=second_site,
            packages=[package],
        )

        subscriptions = customer.subscriptions.filter(package=package, status=CustomerSubscription.Status.ACTIVE).order_by("site_id")
        self.assertEqual(subscriptions.count(), 2)
        self.assertEqual(set(subscriptions.values_list("site_id", flat=True)), {primary_site.id, second_site.id})


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

    def test_customer_detail_displays_assets_not_catalog_products(self):
        ExternalAssetReference.objects.create(
            tenant=self.org1,
            organization=self.org1,
            customer=self.customer_a,
            external_uuid='cccccccc-cccc-cccc-cccc-cccccccccccc',
            asset_tag='CPE-ALICE-01',
            serial_number='SN-ALICE-01',
            category_name='Router',
            branch_name='Moshi',
            status='active',
            display_name='Managed Router',
            description='<script>alert("xss")</script>',
            custom_attributes=[{'label': 'Model', 'value': 'C-111'}],
            source_url='http://127.0.0.1:8001/assets/cccccccc-cccc-cccc-cccc-cccccccccccc/',
            source_updated_at=timezone.now(),
        )
        self.client.login(username='staff', password='pass')

        response = self.client.get(reverse('customer-detail', kwargs={'pk': self.customer_a.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Associated assets')
        self.assertContains(response, 'CPE-ALICE-01')
        self.assertContains(response, 'Managed Router')
        self.assertContains(response, 'C-111')
        self.assertContains(response, 'Open in AssetMS')
        self.assertNotContains(response, '<script>alert("xss")</script>', html=False)
        self.assertContains(response, '&lt;script&gt;alert', html=False)
        self.assertNotContains(response, 'Associated products')

    def test_sales_sees_customer_identity_and_non_monetary_health_for_owned_invoice(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Sales Visibility Fiber",
            package_type="indoor",
            speed="20 Mbps",
            monthly_fee=Decimal("50000.00"),
            setup_fee=Decimal("0.00"),
            description="Test package",
        )
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            start_date=timezone.localdate(),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.staff,
            subscription_id=subscription.id,
            period_start=timezone.localdate().replace(day=1),
            months=1,
        )
        SubscriptionPeriod.objects.filter(pk=period.pk).update(status=SubscriptionPeriod.Status.INVOICED)
        owned_invoice = BillingDocument.objects.get(pk=period.invoice_id)

        product = Product.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Manager-only invoice item",
            category="hardware",
            quantity=Decimal("1.00"),
            measure_unit="Unit",
            buying_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            stock=1,
            is_active=True,
        )
        manager_invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.admin,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_a.id,
            issue_date=timezone.localdate(),
            items=[LineItemInput(product_id=product.id, quantity=Decimal("1.00"), unit_price=Decimal("150.00"))],
        )

        self.client.login(username="staff", password="pass")
        response = self.client.get(reverse("customer-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.customer_a.name)
        self.assertContains(response, "Billing health")
        self.assertContains(response, "Invoice requires attention")
        self.assertContains(response, f"Latest invoice: {owned_invoice.number}")
        self.assertNotContains(response, manager_invoice.number)
        self.assertNotContains(response, f"{owned_invoice.total:,.2f} TZS")

    def test_customer_list_today_worklist_shows_only_customers_worked_on_today(self):
        worked_today = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Worked Today",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        worked_yesterday = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Worked Yesterday",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Arusha",
        )

        AuditLog.objects.create(
            organization=self.org1,
            tenant=self.org1,
            actor=self.staff,
            performed_by=self.staff,
            action="customer.upserted",
            object_type="Customer",
            object_id=str(worked_today.id),
            performed_at=timezone.now(),
        )
        AuditLog.objects.create(
            organization=self.org1,
            tenant=self.org1,
            actor=self.staff,
            performed_by=self.staff,
            action="customer.upserted",
            object_type="Customer",
            object_id=str(worked_yesterday.id),
            performed_at=timezone.now() - timedelta(days=1),
        )

        self.client.login(username="staff", password="pass")
        response = self.client.get(reverse("customer-list"), {"worklist": "today"})

        self.assertEqual(response.status_code, 200)
        customers = list(response.context["customers"])
        self.assertEqual([customer.id for customer in customers], [worked_today.id])

    def test_due_soon_worklist_excludes_unpaid_expired_and_unpaid_invoice_customers(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Office Fiber",
            package_type="indoor",
            speed="20 Mbps",
            monthly_fee=Decimal("50000.00"),
            setup_fee=Decimal("0.00"),
            description="Fiber package",
        )
        today = timezone.localdate()
        month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

        due_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            start_date=today,
        )
        CustomerSubscription.objects.filter(pk=due_subscription.pk).update(
            paid_through_date=month_end
        )

        expired_customer = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Expired service",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        expired_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=expired_customer,
            package=package,
            start_date=today,
        )
        CustomerSubscription.objects.filter(pk=expired_subscription.pk).update(
            paid_through_date=today - timedelta(days=1)
        )

        unpaid_customer = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Unpaid invoice",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        unpaid_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=unpaid_customer,
            package=package,
            start_date=today,
        )
        CustomerSubscription.objects.filter(pk=unpaid_subscription.pk).update(
            paid_through_date=month_end
        )
        period = SubscriptionBillingService.create_period(
            organization=self.org1,
            subscription=unpaid_subscription,
            period_start=today,
        )
        SubscriptionPeriod.objects.filter(pk=period.pk).update(status=SubscriptionPeriod.Status.INVOICED)

        self.client.login(username="admin", password="pass")
        response = self.client.get(reverse("customer-list"), {"worklist": "due"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([customer.id for customer in response.context["customers"]], [self.customer_a.id])
        self.assertEqual(response.context["due_soon_customers"], 1)

    def test_due_soon_worklist_excludes_customer_with_legacy_two_month_paid_invoice(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Multi-site Fiber",
            package_type="indoor",
            speed="50 Mbps",
            monthly_fee=Decimal("100000.00"),
            setup_fee=Decimal("0.00"),
            description="Multi-site package",
        )
        today = timezone.localdate()
        month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        main_site = CustomerService.ensure_primary_site(organization=self.org1, customer=self.customer_a)
        sub_site = CustomerSite.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=self.customer_a,
            name="Sub Office",
            location="Arusha",
        )
        main_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            site=main_site,
            start_date=today,
        )
        sub_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            site=sub_site,
            start_date=today,
        )
        CustomerSubscription.objects.filter(pk=main_subscription.pk).update(paid_through_date=month_end)
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.staff,
            subscription_id=sub_subscription.pk,
            period_start=today.replace(day=1),
            months=2,
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.staff,
            invoice_id=period.invoice_id,
            amount_paid=period.invoice.total,
            payment_method="cash",
        )

        # Preserve the legacy inconsistency seen in production: the paid
        # recurring invoice covers two months, but its denormalized dates still
        # say one month. The worklist must honor the paid invoice term.
        CustomerSubscription.objects.filter(pk=sub_subscription.pk).update(paid_through_date=month_end)
        SubscriptionPeriod.objects.filter(pk=period.pk).update(
            months=1,
            period_end=month_end,
        )
        period.refresh_from_db()
        self.assertEqual(period.status, SubscriptionPeriod.Status.PAID)
        self.assertEqual(period.invoice.status, BillingDocument.Status.PAID)
        self.assertEqual(period.invoice.total, package.monthly_fee * 2)

        self.client.login(username="admin", password="pass")
        response = self.client.get(reverse("customer-list"), {"worklist": "due"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.customer_a.id, [customer.id for customer in response.context["customers"]])
        self.assertEqual(response.context["due_soon_customers"], 0)
    def test_customer_list_searches_package_and_preserves_page_size(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Fiber Ultra",
            package_type="indoor",
            speed="50 Mbps",
            monthly_fee=Decimal("100000.00"),
            setup_fee=Decimal("0.00"),
            description="Fast internet",
        )
        self.customer_a.packages.add(package)
        for index in range(30):
            Customer.all_objects.create(
                organization=self.org1,
                tenant=self.org1,
                name=f"Extra {index:02d}",
                customer_type="internet",
                status=Customer.Status.ACTIVE,
                location="Moshi",
            )

        self.client.login(username="staff", password="pass")
        response = self.client.get(reverse("customer-list"), {"search": "Fiber", "page_size": "25"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["customers"]), [self.customer_a])
        self.assertContains(response, "page_size=25")

    def test_customer_list_uses_compact_page_scrolling_workspace(self):
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("customer-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="jims-customer-list-page')
        self.assertContains(response, 'data-customer-billing')
        self.assertContains(response, 'class="jims-customer-table')
        self.assertContains(response, 'class="jims-customer-mobile-list')
        self.assertNotContains(response, 'jims-list-shell')
        self.assertNotContains(response, "Customers needing action")
        self.assertNotContains(response, "Search and filters")

    def test_customer_list_exposes_multiple_sites_in_accessible_disclosures(self):
        primary = CustomerService.ensure_primary_site(organization=self.org1, customer=self.customer_a)
        secondary = CustomerSite.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=self.customer_a,
            name="KCC Branch",
            location="KCC Floor 3",
            is_primary=False,
            is_active=True,
        )
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Branch Fiber",
            package_type="indoor",
            speed="30 Mbps",
            monthly_fee=Decimal("90000.00"),
            setup_fee=Decimal("0.00"),
            description="Branch package",
        )
        secondary.packages.add(package)
        subscription = CustomerSubscription.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=self.customer_a,
            site=secondary,
            package=package,
            status=CustomerSubscription.Status.ACTIVE,
            start_date=date(2026, 8, 1),
            monthly_fee_at_signup=package.monthly_fee,
        )
        SubscriptionPeriod.objects.create(
            organization=self.org1,
            tenant=self.org1,
            subscription=subscription,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            original_amount=package.monthly_fee,
            final_amount=package.monthly_fee,
            status=SubscriptionPeriod.Status.OVERDUE,
        )
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("customer-list"), {"search": self.customer_a.name})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View 2 sites")
        self.assertContains(response, f'aria-controls="customer-sites-{self.customer_a.pk}"')
        self.assertContains(response, f'id="customer-sites-{self.customer_a.pk}"')
        self.assertContains(response, primary.name)
        self.assertContains(response, "KCC Branch")
        self.assertContains(response, "KCC Floor 3")
        self.assertContains(response, "Branch Fiber")
        self.assertContains(response, "Billing health")
        self.assertContains(response, "Payment due")
        self.assertContains(response, "Balance: 90,000 TZS")

        self.client.logout()
        self.client.login(username="staff", password="pass")
        sales_response = self.client.get(reverse("customer-list"), {"search": self.customer_a.name})
        self.assertEqual(sales_response.status_code, 200)
        self.assertContains(sales_response, "Payment due")
        self.assertContains(sales_response, "Payment requires follow-up")
        self.assertNotContains(sales_response, "Balance: 90,000 TZS")

    def test_walk_in_customer_is_not_presented_as_missing_a_subscription(self):
        Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Counter Sale Customer",
            customer_type="random",
            status=Customer.Status.ACTIVE,
            location="Moshi",
            phone="255712345678",
        )
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("customer-list"), {"search": "Counter Sale"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Walk-in customer")
        self.assertContains(response, "No recurring service billing")
        self.assertNotContains(response, "No active subscription")

    def test_customer_filter_toolbar_reports_active_filter_count(self):
        self.client.login(username="admin", password="pass")

        response = self.client.get(
            reverse("customer-list"),
            {"type": "internet", "status": "active", "search": "Alice"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_filter_count"], 2)
        self.assertContains(response, "Clear search &amp; filters")
        self.assertContains(response, 'aria-live="polite"')

    def test_finance_can_count_and_filter_paid_and_unpaid_customers_by_billing_month(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Monthly Billing Filter",
            package_type="indoor",
            speed="20 Mbps",
            monthly_fee=Decimal("50000.00"),
            setup_fee=Decimal("0.00"),
            description="Billing period filter test",
        )
        unpaid_customer = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="May Unpaid Customer",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        paid_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1, customer=self.customer_a, package=package, start_date=date(2026, 5, 1),
        )
        unpaid_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1, customer=unpaid_customer, package=package, start_date=date(2026, 5, 1),
        )
        SubscriptionPeriod.objects.create(
            organization=self.org1, tenant=self.org1, subscription=paid_subscription,
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
            final_amount=Decimal("50000.00"), status=SubscriptionPeriod.Status.PAID,
        )
        SubscriptionPeriod.objects.create(
            organization=self.org1, tenant=self.org1, subscription=unpaid_subscription,
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
            final_amount=Decimal("50000.00"), status=SubscriptionPeriod.Status.OVERDUE,
        )
        SubscriptionPeriod.objects.create(
            organization=self.org1, tenant=self.org1, subscription=unpaid_subscription,
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
            final_amount=Decimal("30000.00"), status=SubscriptionPeriod.Status.INVOICED,
        )
        self.client.login(username="admin", password="pass")

        overview = self.client.get(reverse("customer-list"), {"month": "2026-05"})
        paid = self.client.get(reverse("customer-list"), {"worklist": "paid", "month": "2026-05"})
        unpaid = self.client.get(reverse("customer-list"), {"worklist": "unpaid", "month": "2026-05"})

        self.assertEqual(overview.context["selected_paid_customers"], 1)
        self.assertEqual(overview.context["selected_unpaid_customers"], 1)
        self.assertEqual(overview.context["selected_month_open_receivables"], Decimal("50000.00"))
        self.assertEqual(overview.context["total_open_receivables"], Decimal("80000.00"))
        self.assertContains(overview, "May 2026")
        self.assertContains(overview, "Open for selected month")
        self.assertContains(overview, "Total open · all months")
        self.assertEqual([customer.id for customer in paid.context["customers"]], [self.customer_a.id])
        self.assertEqual([customer.id for customer in unpaid.context["customers"]], [unpaid_customer.id])
        self.assertNotContains(paid, self.customer_b.name)

    def test_sales_user_does_not_receive_finance_monthly_payment_counts(self):
        self.client.login(username="staff", password="pass")

        response = self.client.get(reverse("customer-list"), {"month": "2026-05", "worklist": "paid"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("selected_paid_customers", response.context)
        self.assertNotContains(response, "Paid customers")

    def test_customer_pages_show_latest_reissued_subscription_invoice_amount(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Fiber Pro",
            package_type="indoor",
            speed="50 Mbps",
            monthly_fee=Decimal("50000.00"),
            setup_fee=Decimal("0.00"),
            description="Subscription package",
            is_active=True,
        )
        primary_site = CustomerService.ensure_primary_site(organization=self.org1, customer=self.customer_a)
        secondary_site = CustomerSite.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=self.customer_a,
            name="Branch Office",
            location="Arusha",
            is_primary=False,
        )

        primary_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            site=primary_site,
            start_date=date(2026, 6, 1),
        )
        primary_period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.staff,
            subscription_id=primary_subscription.id,
            period_start=date(2026, 6, 1),
            months=1,
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.staff,
            invoice_id=primary_period.invoice_id,
            amount_paid=primary_period.invoice.total,
            payment_method="cash",
        )

        secondary_subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            site=secondary_site,
            start_date=date(2026, 8, 1),
        )
        secondary_period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.staff,
            subscription_id=secondary_subscription.id,
            period_start=date(2026, 8, 1),
            months=1,
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.staff,
            invoice_id=secondary_period.invoice_id,
            amount_paid=secondary_period.invoice.total,
            payment_method="cash",
        )

        self.client.login(username="admin", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        response = self.client.get(reverse("customer-detail", args=[self.customer_a.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Billed service period")
        self.assertContains(response, "01 Aug 2026")
        self.assertContains(response, "31 Aug 2026")
        self.assertContains(response, "50,000.00 TZS")
        self.assertContains(response, "Confirmed paid coverage")

        with patch("customers.views.timezone.localdate", return_value=date(2026, 8, 15)):
            list_response = self.client.get(reverse("customer-list"))
        self.assertEqual(list_response.status_code, 200)
        listed_customer = list(list_response.context["customers"])[0]
        self.assertNotEqual(listed_customer.billing_label, "Expired")
        self.assertContains(list_response, "Paid through Aug 31, 2026")

    def test_customer_detail_separates_unpaid_billed_period_from_confirmed_paid_coverage(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Professional",
            package_type="indoor",
            speed="30 Mbps",
            monthly_fee=Decimal("120000.00"),
            setup_fee=Decimal("0.00"),
            description="Professional internet package",
            is_active=True,
        )
        site = CustomerService.ensure_primary_site(organization=self.org1, customer=self.customer_a)
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            site=site,
            start_date=date(2026, 6, 11),
        )
        paid_period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.staff,
            subscription_id=subscription.id,
            period_start=date(2026, 6, 1),
            months=2,
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.staff,
            invoice_id=paid_period.invoice_id,
            amount_paid=paid_period.invoice.total,
            payment_method="cash",
        )
        unpaid_period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.staff,
            subscription_id=subscription.id,
            period_start=date(2026, 8, 1),
            months=3,
        )

        self.client.login(username="admin", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()
        response = self.client.get(reverse("customer-detail", args=[self.customer_a.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Billed service period")
        self.assertContains(response, "01 Aug 2026")
        self.assertContains(response, "31 Oct 2026")
        self.assertContains(response, "360,000.00 TZS")
        self.assertContains(response, "Awaiting full payment")
        self.assertContains(
            response,
            "This period becomes confirmed paid coverage only after the invoice is fully settled.",
        )
        self.assertContains(response, "Confirmed paid coverage Jun 11, 2026 – Jul 31, 2026")
        self.assertEqual(unpaid_period.status, SubscriptionPeriod.Status.INVOICED)

    def test_customer_detail_and_list_show_paid_through_aug_after_reissued_two_month_invoice(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Business",
            package_type="indoor",
            speed="10 Mbps",
            monthly_fee=Decimal("50000.00"),
            setup_fee=Decimal("0.00"),
            description="Business package",
            is_active=True,
        )
        primary_site = CustomerService.ensure_primary_site(organization=self.org1, customer=self.customer_a)
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            site=primary_site,
            start_date=date(2026, 7, 1),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.staff,
            subscription_id=subscription.id,
            period_start=date(2026, 7, 1),
            months=1,
        )

        reissued = BillingService.reissue_invoice(
            organization=self.org1,
            performed_by=self.staff,
            invoice_id=period.invoice_id,
            reason="Invoice should have covered July and August.",
        )
        updated = BillingService.update_draft_invoice(
            organization=self.org1,
            performed_by=self.staff,
            invoice_id=reissued.id,
            tax_rate=Decimal("0.00"),
            status=BillingDocument.Status.ISSUED,
            items=[
                LineItemInput(
                    package_id=package.id,
                    description="July and August Business subscription",
                    quantity=Decimal("1.00"),
                    unit_price=Decimal("100000.00"),
                    billing_behavior=BillingLineItem.BillingBehavior.RECURRING_MONTHLY,
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                )
            ],
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.staff,
            invoice_id=updated.id,
            amount_paid=updated.total,
            payment_method="cash",
        )

        self.client.login(username="admin", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        with patch("customers.views.timezone.localdate", return_value=date(2026, 8, 15)):
            detail_response = self.client.get(reverse("customer-detail", args=[self.customer_a.id]))
            list_response = self.client.get(reverse("customer-list"))

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Billed service period")
        self.assertContains(detail_response, "01 Jul 2026")
        self.assertContains(detail_response, "31 Aug 2026")
        self.assertContains(detail_response, "100,000.00 TZS")
        self.assertContains(detail_response, "Confirmed paid coverage Jul 01, 2026 – Aug 31, 2026")

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Paid through Aug 31, 2026")
        self.assertNotContains(list_response, "Expired")

    def test_internet_package_section_falls_back_to_subscription_dates_when_profile_is_empty(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Fiber Plus",
            package_type="indoor",
            speed="20 Mbps",
            monthly_fee=Decimal("50000.00"),
            setup_fee=Decimal("0.00"),
            description="Fiber package",
            is_active=True,
        )
        primary_site = CustomerService.ensure_primary_site(organization=self.org1, customer=self.customer_a)
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_a,
            package=package,
            site=primary_site,
            start_date=date(2026, 7, 1),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.staff,
            subscription_id=subscription.id,
            period_start=date(2026, 7, 1),
            months=2,
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.staff,
            invoice_id=period.invoice_id,
            amount_paid=period.invoice.total,
            payment_method="cash",
        )
        InternetCustomer.objects.create(
            customer=self.customer_a,
            tenant=self.org1,
            package_type="indoor",
        )

        self.client.login(username="admin", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        response = self.client.get(reverse("customer-detail", args=[self.customer_a.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Internet package")
        self.assertContains(response, "Fiber Plus")
        self.assertContains(response, "Confirmed paid coverage Jul 01, 2026 – Aug 31, 2026")

    def test_customer_create_page_renders_professional_sections(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(reverse("customer-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sales and billing preferences")
        self.assertContains(response, "Primary internet service")
        self.assertContains(response, "Choose service packages")
        self.assertContains(response, 'max-w-[1360px]')
        self.assertContains(response, 'data-unsaved-form')
        self.assertEqual(response.content.decode().count("Save customer"), 1)
        self.assertNotContains(response, "On this page")

    def test_customer_detail_bounds_record_lists_and_explains_unverified_service(self):
        site = CustomerService.ensure_primary_site(organization=self.org1, customer=self.customer_a)
        InternetService.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=self.customer_a,
            site=site,
            service_code="LEGACY-DETAIL-TEST",
            name="Legacy Internet Service",
            operational_status=InternetService.OperationalStatus.UNKNOWN,
        )
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("customer-detail", args=[self.customer_a.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not verified")
        self.assertContains(response, "Operational state has not been confirmed")
        self.assertContains(response, "items-start gap-6 lg:grid-cols-2")
        self.assertEqual(response.context["operational_service_count"], 0)

    def test_unverified_service_can_be_activated_with_audited_transition(self):
        site = CustomerService.ensure_primary_site(organization=self.org1, customer=self.customer_a)
        service = InternetService.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=self.customer_a,
            site=site,
            service_code="LEGACY-ACTIVATE-TEST",
            name="Legacy Internet Service",
            operational_status=InternetService.OperationalStatus.UNKNOWN,
        )

        InternetServiceDomainService.unblock_service(
            organization=self.org1,
            actor=self.admin,
            service_id=service.pk,
            reason="Legacy service operation verified",
        )

        service.refresh_from_db()
        self.assertEqual(service.operational_status, InternetService.OperationalStatus.ACTIVE)
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.org1,
                action="internet_service.status_changed",
                object_id=str(service.pk),
            ).exists()
        )

    def test_walk_in_customer_journey_hides_internet_service_without_requiring_it(self):
        self.client.login(username="staff", password="pass")

        response = self.client.post(
            reverse("customer-create"),
            {
                "name": "Counter Customer",
                "customer_type": "random",
                "status": Customer.Status.ACTIVE,
                "pricing_tier": Customer.PricingTier.RETAIL,
                "location": "Moshi",
                "packages": [],
                "status_change_reason": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        customer = Customer.objects.get(name="Counter Customer")
        self.assertEqual(customer.customer_type, "random")
        self.assertFalse(InternetCustomer.objects.filter(customer=customer).exists())
        self.assertFalse(customer.packages.exists())
        self.assertFalse(customer.sites.exists())

    def test_internet_customer_can_be_created_without_optional_package(self):
        self.client.login(username="staff", password="pass")

        response = self.client.post(
            reverse("customer-create"),
            {
                "name": "Internet Customer Without Package",
                "customer_type": "internet",
                "status": Customer.Status.ACTIVE,
                "pricing_tier": Customer.PricingTier.RETAIL,
                "location": "Arusha",
                "packages": [],
                "status_change_reason": "",
                "package_type": "outdoor",
                "start_date": "2026-08-01",
                "end_date": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        customer = Customer.objects.get(name="Internet Customer Without Package")
        self.assertFalse(customer.packages.exists())
        self.assertEqual(customer.internet_profile.package_type, "outdoor")

    def test_populated_network_tax_service_and_dates_open_their_edit_context(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Populated Business Fiber",
            package_type="indoor",
            speed="100 Mbps",
            monthly_fee=Decimal("250000.00"),
            setup_fee=Decimal("50000.00"),
            description="Populated form fixture",
        )
        self.customer_a.ip_address = "10.20.30.40"
        self.customer_a.vlan_id = "VLAN 120"
        self.customer_a.tin_number = "123-456-789"
        self.customer_a.vrn_number = "VRN-001"
        self.customer_a.save(update_fields=["ip_address", "vlan_id", "tin_number", "vrn_number"])
        self.customer_a.packages.add(package)
        InternetCustomer.objects.create(
            customer=self.customer_a,
            tenant=self.org1,
            package_type="indoor",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("customer-update", args=[self.customer_a.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Account summary")
        self.assertContains(response, "Populated Business Fiber")
        self.assertContains(response, "10.20.30.40")
        self.assertRegex(response.content.decode(), r'<details class="group mt-5 rounded-lg border border-slate-200 bg-white" open>')
        self.assertRegex(response.content.decode(), r'<details class="group md:col-span-8" open>')

    def test_walk_in_edit_renders_internet_service_as_conditional(self):
        self.customer_a.customer_type = "random"
        self.customer_a.save(update_fields=["customer_type"])
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("customer-update", args=[self.customer_a.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'id="internet-section" class="rounded-lg border border-slate-200 bg-white p-4 sm:p-6 hidden"',
        )
        self.assertNotContains(response, "No Internet package")

    def test_network_validation_error_reopens_optional_disclosure(self):
        self.client.login(username="admin", password="pass")

        response = self.client.post(
            reverse("customer-update", args=[self.customer_a.pk]),
            {
                "name": self.customer_a.name,
                "customer_type": "internet",
                "status": Customer.Status.ACTIVE,
                "pricing_tier": Customer.PricingTier.RETAIL,
                "location": self.customer_a.location,
                "ip_address": "not-an-ip-address",
                "packages": [],
                "status_change_reason": "",
                "package_type": "indoor",
                "start_date": "",
                "end_date": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid IPv4 or IPv6 address.")
        self.assertRegex(
            response.content.decode(),
            r'<details class="group mt-5 rounded-lg border border-slate-200 bg-white" open>',
        )

    def test_customer_edit_renders_existing_inactive_package_as_selected(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Grandfathered Fiber",
            package_type="indoor",
            speed="15 Mbps",
            monthly_fee=Decimal("65000.00"),
            setup_fee=Decimal("0.00"),
            description="Historical assignment",
            is_active=False,
        )
        self.customer_a.packages.add(package)
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("customer-update", args=[self.customer_a.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grandfathered Fiber")
        self.assertContains(response, "Inactive")
        self.assertRegex(response.content.decode(), rf'value="{package.pk}"[^>]*checked')

    def test_customer_edit_renders_subscription_only_package_as_selected(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Legacy Subscription Fiber",
            package_type="indoor",
            speed="25 Mbps",
            monthly_fee=Decimal("85000.00"),
            setup_fee=Decimal("0.00"),
            description="Subscription is the legacy assignment source",
            is_active=True,
        )
        primary_site = CustomerService.ensure_primary_site(
            organization=self.org1,
            customer=self.customer_a,
        )
        CustomerSubscription.objects.create(
            organization=self.org1,
            tenant=self.org1,
            customer=self.customer_a,
            site=primary_site,
            package=package,
            status=CustomerSubscription.Status.ACTIVE,
            start_date=date(2026, 6, 1),
            monthly_fee_at_signup=package.monthly_fee,
        )
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("customer-update", args=[self.customer_a.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Legacy Subscription Fiber")
        self.assertRegex(response.content.decode(), rf'value="{package.pk}"[^>]*checked')

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
