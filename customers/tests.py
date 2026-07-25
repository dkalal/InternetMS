from django.test import TestCase
from django.urls import reverse

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta, date

from audit.models import AuditLog
from billing.models import BillingDocument, BillingLineItem, SubscriptionPeriod
from billing.models import CustomerSubscription
from billing.services import BillingService, BillingServiceError, LineItemInput, SubscriptionBillingService
from customers.models import Customer, CustomerSite, InternetCustomer
from customers.forms import CustomerForm
from customers.services import CustomerService, CustomerServiceError
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

        self.client.login(username="staff", password="pass")
        response = self.client.get(reverse("customer-list"), {"worklist": "due"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([customer.id for customer in response.context["customers"]], [self.customer_a.id])
        self.assertEqual(response.context["due_soon_customers"], 1)

    def test_due_soon_worklist_excludes_customer_with_another_office_paid_into_next_month(self):
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
        next_month_start = month_end + timedelta(days=1)
        next_month_end = (next_month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
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
        CustomerSubscription.objects.filter(pk=sub_subscription.pk).update(paid_through_date=next_month_end)

        self.client.login(username="staff", password="pass")
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

        self.client.login(username="staff", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        response = self.client.get(reverse("customer-detail", args=[self.customer_a.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Latest invoice for Aug 2026: 50,000.00 TZS")

        list_response = self.client.get(reverse("customer-list"), {"worklist": "today"})
        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, "Expired")
        self.assertContains(list_response, "Paid through Aug 31, 2026")

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

        self.client.login(username="staff", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        detail_response = self.client.get(reverse("customer-detail", args=[self.customer_a.id]))
        list_response = self.client.get(reverse("customer-list"), {"worklist": "today"})

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Latest invoice for Jul 2026 - Aug 2026: 100,000.00 TZS")
        self.assertContains(detail_response, "Paid through Aug 31, 2026")

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

        self.client.login(username="staff", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        response = self.client.get(reverse("customer-detail", args=[self.customer_a.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Internet package")
        self.assertContains(response, "Fiber Plus")
        self.assertContains(response, "Paid through Aug 31, 2026")

    def test_customer_create_page_renders_professional_sections(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(reverse("customer-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tax and billing identity")
        self.assertContains(response, "Find package")

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
