from django.shortcuts import render, get_object_or_404, redirect
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from django.views.generic import TemplateView, ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views.generic.edit import FormView
from django.urls import reverse_lazy, reverse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Max, OuterRef, Q, Subquery, Sum
from django.utils import timezone

from audit.models import AuditLog
from custom_fields.mixins import CustomFieldPageContextMixin
from billing.models import BillingDocument, BillingLineItem, CustomerSubscription, SubscriptionPeriod
from custom_fields.services import CustomFieldService
from billing.services import (
    add_months,
    last_day_of_month,
    paid_service_coverage,
    paid_service_coverage_for_subscriptions,
)

from .models import Customer, CustomerSite, InternetCustomer, InternetService
from .forms import (
    CustomerForm,
    CustomerSiteForm,
    InternetCustomerForm,
    InternetServiceCreateForm,
    ServicePackageChangeForm,
    ServiceStatusChangeForm,
    HardDeleteCustomerForm,
    AnonymizeCustomerForm,
)
from .services import CustomerService, CustomerServiceError, InternetServiceDomainService
from users.permissions import PermissionCode, has_tenant_permission, require_permission, sales_document_queryset_for
from users.tenancy import require_organization
from internetservices.listing import apply_sort, clean_page_size, page_context


def _latest_subscription_period(subscription):
    periods = list(subscription.periods.all())
    if not periods:
        return None
    return max(periods, key=lambda period: (period.period_start, period.id))


def _subscription_billing_snapshot(subscription):
    latest_period = _latest_subscription_period(subscription)
    latest_amount = None
    if latest_period is not None:
        if latest_period.invoice_id and latest_period.invoice is not None:
            latest_amount = latest_period.invoice.total
        else:
            latest_amount = latest_period.final_amount
    return latest_period, latest_amount


def _subscription_paid_through_date(subscription):
    paid_through = subscription.paid_through_date
    monthly_fee = (subscription.monthly_fee_at_signup or Decimal("0.00")).quantize(Decimal("0.01"))
    if monthly_fee <= Decimal("0.00"):
        return paid_through

    for period in subscription.periods.all():
        if period.status != SubscriptionPeriod.Status.PAID:
            continue

        effective_months = max(int(period.months or 1), 1)
        invoice = period.invoice
        if invoice is not None:
            invoice_items = list(invoice.items.all())
            package_items = [
                item
                for item in invoice_items
                if item.package_id == subscription.package_id
                and item.billing_behavior == BillingLineItem.BillingBehavior.RECURRING_MONTHLY
            ]
            recurring_item = package_items[0] if package_items else next(
                (
                    item
                    for item in invoice_items
                    if item.billing_behavior == BillingLineItem.BillingBehavior.RECURRING_MONTHLY
                ),
                None,
            )
            if recurring_item is not None:
                quantity_months = max(int(recurring_item.quantity or 1), 1)
                if quantity_months > 1:
                    effective_months = quantity_months
                else:
                    line_total = (recurring_item.quantity * recurring_item.unit_price - recurring_item.discount_amount).quantize(Decimal("0.01"))
                    if line_total > Decimal("0.00"):
                        inferred_months = int((line_total / monthly_fee).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                        if inferred_months > 1:
                            expected_total = (monthly_fee * Decimal(inferred_months)).quantize(Decimal("0.01"))
                            if expected_total == line_total:
                                effective_months = inferred_months

        effective_end = last_day_of_month(add_months(period.period_start, effective_months + period.free_months - 1))
        if paid_through is None or effective_end > paid_through:
            paid_through = effective_end

    return paid_through


def _customer_latest_billing_snapshot(subscriptions):
    latest_period = None
    latest_amount = None
    for subscription in subscriptions:
        period, amount = _subscription_billing_snapshot(subscription)
        if period is None:
            continue
        if latest_period is None or (period.period_start, period.id) > (latest_period.period_start, latest_period.id):
            latest_period = period
            latest_amount = amount
    return latest_period, latest_amount


def _customer_paid_through_date(subscriptions):
    paid_through = None
    for subscription in subscriptions:
        subscription_paid_through = _subscription_paid_through_date(subscription)
        if subscription_paid_through is None:
            continue
        if paid_through is None or subscription_paid_through > paid_through:
            paid_through = subscription_paid_through
    return paid_through


def _period_label(period):
    if period is None:
        return ""
    start_label = period.period_start.strftime("%b %Y")
    if getattr(period, "months", 1) <= 1:
        return start_label
    end_label = period.period_end.strftime("%b %Y")
    if end_label == start_label:
        return start_label
    return f"{start_label} - {end_label}"


def _internet_profile_snapshot(customer, subscriptions, internet_profile):
    if internet_profile is None and not subscriptions:
        return None

    primary_subscription = subscriptions[0] if subscriptions else None
    source_subscription = primary_subscription
    source_period = _subscription_billing_snapshot(source_subscription)[0] if source_subscription else None
    paid_through = _subscription_paid_through_date(source_subscription) if source_subscription else None
    fallback_package_label = source_subscription.package.name if source_subscription is not None else ""
    fallback_start_date = source_subscription.start_date if source_subscription is not None else None
    fallback_end_date = paid_through or (source_period.period_end if source_period else None)

    if internet_profile is not None:
        package_label = internet_profile.get_package_type_display() or fallback_package_label
        start_date = internet_profile.start_date or fallback_start_date
        end_date = internet_profile.end_date or fallback_end_date
    elif source_subscription is not None:
        package_label = fallback_package_label
        start_date = fallback_start_date
        end_date = fallback_end_date
    else:
        package_label = ""
        start_date = None
        end_date = None

    if start_date is None and source_period is not None:
        start_date = source_period.period_start
    if end_date is None:
        end_date = paid_through

    return SimpleNamespace(
        package_label=package_label,
        start_date=start_date,
        end_date=end_date,
        days_remaining=(end_date - timezone.localdate()).days if end_date else None,
        customer=customer,
    )


# --- Function-based view example ---
@login_required
def home_view(request):
    organization = require_organization(request)
    require_permission(request, PermissionCode.TENANT_READ)
    context = {
        'page_title': 'Home',
        'welcome_message': 'Welcome to our customer portal!',
        'featured_customers': Customer.objects.for_organization(organization).active()[:5],
    }
    return render(request, 'customers/home.html', context)

# --- Class-based TemplateView example ---
class AboutView(TemplateView):
    template_name = 'customers/about.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'About Us'
        context['mission_statement'] = 'Our mission is to provide excellent customer service.'
        context['team_members'] = [
            {'name': 'Justin Safari', 'position': 'CEO'},
            {'name': 'Rashid Mohamed', 'position': 'CTO'},
            {'name': 'Othman H', 'position': 'Lead Developer'},
        ]
        return context

# --- ListView example ---
class CustomerListView(LoginRequiredMixin, ListView):
    model = Customer
    template_name = 'customers/customer_list.html'
    context_object_name = 'customers'
    paginate_by = 25
    sort_options = {
        "created": ("-created_at", "-id"),
        "name": ("name", "id"),
        "-name": ("-name", "-id"),
        "status": ("status", "name"),
        "-status": ("-status", "name"),
        "type": ("customer_type", "name"),
        "-type": ("-customer_type", "name"),
        "billing": ("unpaid_amount", "name"),
        "-billing": ("-unpaid_amount", "name"),
        "paid_through": ("latest_paid_through", "name"),
        "-paid_through": ("-latest_paid_through", "name"),
    }

    def get_paginate_by(self, queryset):
        return clean_page_size(self.request.GET.get("page_size"), default=self.paginate_by)

    def _today_activity_logs(self, organization, user):
        today = timezone.localdate()
        return AuditLog.objects.filter(
            organization=organization,
            performed_at__date=today,
        ).filter(Q(actor=user) | Q(performed_by=user))

    def _worked_today_customer_ids(self, organization, user):
        activity_logs = self._today_activity_logs(organization, user)
        customer_ids = set()

        for object_id in activity_logs.filter(object_type="Customer").values_list("object_id", flat=True):
            try:
                customer_ids.add(int(object_id))
            except (TypeError, ValueError):
                continue

        for customer_id in activity_logs.filter(
            object_type="CustomerSite",
            metadata__customer_id__isnull=False,
        ).values_list("metadata__customer_id", flat=True):
            try:
                customer_ids.add(int(customer_id))
            except (TypeError, ValueError):
                continue

        billed_customer_ids = BillingDocument.objects.filter(
            organization=organization,
            id__in=[
                int(object_id)
                for object_id in activity_logs.filter(object_type="BillingDocument").values_list("object_id", flat=True)
                if str(object_id).isdigit()
            ],
        ).values_list("customer_id", flat=True)
        customer_ids.update(billed_customer_ids)
        return list(customer_ids)

    def _unpaid_customer_ids(self, organization):
        return SubscriptionPeriod.objects.filter(
            organization=organization,
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
        ).values("subscription__customer_id")

    def _selected_billing_month(self):
        """Return a safe first-of-month value for finance worklists."""
        raw_value = (self.request.GET.get("month") or "").strip()
        if raw_value:
            try:
                year_text, month_text = raw_value.split("-", 1)
                return date(int(year_text), int(month_text), 1)
            except (TypeError, ValueError):
                pass
        return timezone.localdate().replace(day=1)

    def _billing_periods_for_selected_month(self, organization):
        selected_month = self._selected_billing_month()
        return SubscriptionPeriod.objects.filter(
            organization=organization,
            period_start__year=selected_month.year,
            period_start__month=selected_month.month,
        )

    def _due_soon_customer_ids(self, organization):
        """Return customers with a paid, active service expiring later this month.

        This is a customer-level queue, so it uses the latest paid-through
        date across every active office/service. A customer who has already
        paid a later month for any active office must not be placed in Due
        soon just because another office ends earlier. The effective date also
        accounts for paid legacy invoices whose recurring line covers multiple
        months even when the stored paid-through summary was not synchronized.
        A missing paid-through date means no payment has been recorded, and a
        date before today means the service is already expired. Customers with
        an open subscription invoice are kept in the unpaid worklist so queues
        never overlap.
        """
        cached = getattr(self, "_due_soon_ids_cache", None)
        if cached is not None:
            return cached

        today = timezone.localdate()
        month_end = today.replace(day=28)
        while True:
            next_day = month_end + timedelta(days=1)
            if next_day.month != month_end.month:
                break
            month_end = next_day

        open_customer_ids = set(SubscriptionPeriod.objects.filter(
            organization=organization,
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
        ).values_list("subscription__customer_id", flat=True))
        subscriptions = (
            CustomerSubscription.objects.filter(
                organization=organization,
                status=CustomerSubscription.Status.ACTIVE,
            )
            .prefetch_related("periods__invoice__items")
            .order_by("customer_id", "id")
        )
        paid_through_by_customer = {}
        for subscription in subscriptions:
            paid_through = _subscription_paid_through_date(subscription)
            current = paid_through_by_customer.get(subscription.customer_id)
            if paid_through is not None and (current is None or paid_through > current):
                paid_through_by_customer[subscription.customer_id] = paid_through

        self._due_soon_ids_cache = [
            customer_id
            for customer_id, paid_through in paid_through_by_customer.items()
            if customer_id not in open_customer_ids and today <= paid_through <= month_end
        ]
        return self._due_soon_ids_cache

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        finance_all = has_tenant_permission(
            self.request.user, organization, PermissionCode.FINANCE_SALES_VIEW_ALL,
            membership=self.request.membership,
        )
        queryset = (
            Customer.objects.for_organization(organization)
            .exclude(status=Customer.Status.SUSPENDED)
            .optimized_list()
        )
        if finance_all:
            queryset = queryset.prefetch_related("subscriptions__package", "subscriptions__site", "subscriptions__internet_service", "subscriptions__periods", "subscriptions__periods__invoice__items")
        else:
            queryset = queryset.prefetch_related("subscriptions__package", "subscriptions__site", "subscriptions__internet_service")
            customer_type = self.request.GET.get('type')
            if customer_type:
                queryset = queryset.filter(customer_type=customer_type)
            status = self.request.GET.get('status')
            if status:
                queryset = queryset.filter(status=status)
            search_query = self.request.GET.get('search')
            if search_query:
                queryset = queryset.search(search_query)
            worklist = self.request.GET.get("worklist")
            if worklist == "today":
                queryset = queryset.filter(
                    id__in=self._worked_today_customer_ids(organization, self.request.user)
                )
            elif worklist == "no_contact":
                queryset = queryset.filter(
                    (Q(email__isnull=True) | Q(email=""))
                    & (Q(phone__isnull=True) | Q(phone=""))
                )
            elif worklist == "inactive":
                queryset = queryset.inactive()
            elif worklist == "active":
                queryset = queryset.active()
            elif worklist == "suspended":
                queryset = Customer.objects.for_organization(organization).suspended().optimized_list()
            queryset, self.active_sort = apply_sort(
                queryset.distinct(), self.request.GET.get("sort"),
                {key: value for key, value in self.sort_options.items() if key not in {"billing", "-billing", "paid_through", "-paid_through"}},
                "created",
            )
            return queryset
        unpaid_periods = SubscriptionPeriod.objects.filter(
            organization=organization,
            subscription__customer_id=OuterRef("pk"),
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            invoice__isnull=False,
        ).order_by("-period_start", "-id")
        queryset = queryset.annotate(
            unpaid_amount=Sum(
                "subscriptions__periods__final_amount",
                filter=Q(
                    subscriptions__periods__organization=organization,
                    subscriptions__periods__status__in=[
                        SubscriptionPeriod.Status.INVOICED,
                        SubscriptionPeriod.Status.OVERDUE,
                    ],
                ),
                distinct=True,
            ),
            latest_unpaid_invoice_id=Subquery(unpaid_periods.values("invoice_id")[:1]),
            latest_paid_through=Max(
                "subscriptions__periods__period_end",
                filter=Q(
                    subscriptions__organization=organization,
                    subscriptions__status=CustomerSubscription.Status.ACTIVE,
                    subscriptions__periods__status=SubscriptionPeriod.Status.PAID,
                ),
            ),
        )
        
        # Filter by type
        customer_type = self.request.GET.get('type')
        if customer_type:
            queryset = queryset.filter(customer_type=customer_type)
        
        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        worklist = self.request.GET.get('worklist')
        if worklist == 'unpaid':
            if self.request.GET.get("month"):
                unpaid_ids = self._billing_periods_for_selected_month(organization).filter(
                    status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
                ).values("subscription__customer_id")
            else:
                unpaid_ids = self._unpaid_customer_ids(organization)
            queryset = queryset.filter(id__in=unpaid_ids)
        elif worklist == 'paid':
            paid_ids = self._billing_periods_for_selected_month(organization).filter(
                status=SubscriptionPeriod.Status.PAID,
            ).values("subscription__customer_id")
            queryset = queryset.filter(id__in=paid_ids)
        elif worklist == 'due':
            queryset = queryset.filter(id__in=self._due_soon_customer_ids(organization))
        elif worklist == 'no_contact':
            queryset = queryset.filter(
                (Q(email__isnull=True) | Q(email="")) &
                (Q(phone__isnull=True) | Q(phone=""))
            )
        elif worklist == 'inactive':
            queryset = queryset.inactive()
        elif worklist == 'active':
            queryset = queryset.active()
        elif worklist == 'suspended':
            # Re-include suspended customers specifically for this worklist
            queryset = (
                Customer.objects.for_organization(organization)
                .suspended()
                .optimized_list()
                .prefetch_related("subscriptions__package", "subscriptions__site", "subscriptions__internet_service", "subscriptions__periods", "subscriptions__periods__invoice__items")
            )
        elif worklist == 'today':
            queryset = queryset.filter(id__in=self._worked_today_customer_ids(organization, self.request.user))
        
        # Search functionality
        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.search(search_query)

        queryset, self.active_sort = apply_sort(
            queryset.distinct(),
            self.request.GET.get("sort"),
            self.sort_options,
            "created",
        )
        return queryset

    def _whatsapp_link(self, phone):
        if not phone:
            return ""
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits.startswith("0") and len(digits) == 10:
            digits = "255" + digits[1:]
        elif len(digits) == 9:
            digits = "255" + digits
        return f"https://wa.me/{digits}" if digits else ""

    @staticmethod
    def _attach_site_summary(customer, subscriptions, *, finance_all=False, open_site_ids=None):
        """Build list-page site disclosure data from prefetched tenant-scoped relations."""
        sites = sorted(
            list(customer.sites.all()),
            key=lambda site: (not site.is_primary, (site.name or '').lower(), site.id),
        )
        active_by_site = {}
        for subscription in subscriptions:
            if subscription.status == CustomerSubscription.Status.ACTIVE and subscription.site_id:
                active_by_site.setdefault(subscription.site_id, []).append(subscription)

        open_site_ids = open_site_ids or set()
        today = timezone.localdate()

        for site in sites:
            services = list(site.internet_services.all())
            active_subscriptions = active_by_site.get(site.id, [])
            site.list_packages = []
            seen_package_ids = set()
            for subscription in active_subscriptions:
                if subscription.package_id not in seen_package_ids:
                    site.list_packages.append(subscription.package)
                    seen_package_ids.add(subscription.package_id)
            for package in site.packages.all():
                if package.id not in seen_package_ids:
                    site.list_packages.append(package)
                    seen_package_ids.add(package.id)
            site.list_services = services
            site.active_service_count = sum(
                service.operational_status == InternetService.OperationalStatus.ACTIVE
                for service in services
            )
            open_periods = []
            if finance_all:
                for subscription in active_subscriptions:
                    open_periods.extend(
                        period for period in subscription.periods.all()
                        if period.status in {SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE}
                    )
            has_open_billing = bool(open_periods) if finance_all else site.id in open_site_ids
            paid_through_dates = [
                _subscription_paid_through_date(subscription) if finance_all else subscription.paid_through_date
                for subscription in active_subscriptions
            ]
            paid_through = max((value for value in paid_through_dates if value is not None), default=None)

            if has_open_billing:
                site.billing_state = 'unpaid'
                site.billing_label = 'Payment due'
                if finance_all:
                    site.billing_amount = sum((period.final_amount for period in open_periods), Decimal('0.00'))
                    site.billing_note = f"Balance: {site.billing_amount:,.0f} TZS"
                else:
                    site.billing_amount = None
                    site.billing_note = 'Payment requires follow-up'
            elif paid_through is not None and paid_through < today:
                site.billing_state = 'unpaid'
                site.billing_label = 'Expired'
                site.billing_amount = None
                site.billing_note = f"Paid through {paid_through:%b %d, %Y}"
            elif paid_through is not None and paid_through.month == today.month and paid_through.year == today.year:
                site.billing_state = 'due'
                site.billing_label = 'Due soon'
                site.billing_amount = None
                site.billing_note = f"Paid through {paid_through:%b %d, %Y}"
            elif paid_through is not None:
                site.billing_state = 'paid'
                site.billing_label = 'Paid'
                site.billing_amount = None
                site.billing_note = f"Paid through {paid_through:%b %d, %Y}"
            elif active_subscriptions:
                site.billing_state = 'due'
                site.billing_label = 'Not paid'
                site.billing_amount = None
                site.billing_note = 'No payment coverage recorded'
            else:
                site.billing_state = 'neutral'
                site.billing_label = 'No active billing'
                site.billing_amount = None
                site.billing_note = 'No active subscription at this site'

        customer.list_sites = sites
        customer.site_count = len(sites)
        customer.primary_site_summary = next((site for site in sites if site.is_primary), sites[0] if sites else None)

    def _enrich_customers(self, customers, organization):
        customer_ids = [customer.id for customer in customers]
        if not customer_ids:
            return

        unpaid_rows = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                subscription__customer_id__in=customer_ids,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            )
            .values("subscription__customer_id")
            .annotate(amount=Sum("final_amount"))
        )
        unpaid_amounts = {row["subscription__customer_id"]: row["amount"] for row in unpaid_rows}

        invoice_ids = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                subscription__customer_id__in=customer_ids,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
                invoice__isnull=False,
            )
            .order_by("subscription__customer_id", "-period_start")
            .values_list("subscription__customer_id", "invoice_id")
        )
        latest_invoice_by_customer = {}
        for customer_id, invoice_id in invoice_ids:
            latest_invoice_by_customer.setdefault(customer_id, invoice_id)

        today = timezone.localdate()
        for customer in customers:
            subscriptions = list(
                customer.subscriptions.select_related("site", "package", "internet_service")
                .prefetch_related("periods__invoice")
                .order_by("-site__is_primary", "site__name", "package__name", "id")
            )
            # Current-package actions must never silently target cancelled history.
            active_subscriptions = [
                subscription
                for subscription in subscriptions
                if subscription.status == CustomerSubscription.Status.ACTIVE
            ]
            self._attach_site_summary(customer, subscriptions, finance_all=True)
            primary_subscriptions = [
                subscription
                for subscription in active_subscriptions
                if subscription.site and subscription.site.is_primary
            ] or active_subscriptions
            customer.primary_subscription = primary_subscriptions[0] if primary_subscriptions else None
            customer.primary_service = customer.primary_subscription.internet_service if customer.primary_subscription else customer.primary_internet_service
            customer.unpaid_amount = getattr(customer, "unpaid_amount", None) or unpaid_amounts.get(customer.id)
            customer.latest_unpaid_invoice_id = getattr(customer, "latest_unpaid_invoice_id", None) or latest_invoice_by_customer.get(customer.id)
            customer.whatsapp_url = self._whatsapp_link(customer.phone)
            customer.latest_paid_through = _customer_paid_through_date(subscriptions)

            if customer.unpaid_amount:
                customer.billing_state = "unpaid"
                customer.billing_label = "Unpaid"
                customer.billing_note = f"Balance: {customer.unpaid_amount:,.0f} TZS"
                customer.primary_action = "Register receipt" if customer.latest_unpaid_invoice_id else "Renew"
            elif customer.latest_paid_through:
                paid_through = customer.latest_paid_through
                if paid_through < today:
                    customer.billing_state = "unpaid"
                    customer.billing_label = "Expired"
                    customer.billing_note = f"Paid through {paid_through:%b %d, %Y}"
                    customer.primary_action = "Renew"
                elif paid_through.month == today.month and paid_through.year == today.year:
                    customer.billing_state = "due"
                    customer.billing_label = "Due soon"
                    customer.billing_note = f"Paid through {paid_through:%b %d, %Y}"
                    customer.primary_action = "Renew"
                else:
                    customer.billing_state = "paid"
                    customer.billing_label = "Paid"
                    customer.billing_note = f"Paid through {paid_through:%b %d, %Y}"
                    customer.primary_action = "View"
            elif customer.primary_subscription:
                customer.billing_state = "due"
                customer.billing_label = "Not paid"
                customer.billing_note = "No payment recorded"
                customer.primary_action = "Renew"
            elif not customer.email and not customer.phone:
                customer.billing_state = "incomplete"
                customer.billing_label = "Incomplete"
                customer.billing_note = "Missing contact details"
                customer.primary_action = "Complete profile"
            else:
                customer.billing_state = "neutral"
                customer.billing_label = "No subscription"
                customer.billing_note = "No active package billing"
                customer.primary_action = "View"

            latest_period = None
            latest_amount = None
            if subscriptions:
                latest_period, latest_amount = _customer_latest_billing_snapshot(subscriptions)
            if latest_period is not None and latest_amount is not None:
                latest_note = f"Latest invoice: {latest_amount:,.0f} TZS for {_period_label(latest_period)}"
                if customer.billing_note:
                    customer.billing_note = f"{customer.billing_note} | {latest_note}"
                else:
                    customer.billing_note = latest_note

    def _enrich_customers_for_sales(self, customers, organization):
        """Expose operational billing health without exposing monetary data.

        Sales may work every customer in their tenant, but may only inspect
        billing documents they created or were assigned.  Health therefore
        uses non-monetary subscription state, while the latest-invoice link is
        resolved through the same ownership scope as the billing views.
        """
        customer_ids = [customer.id for customer in customers]
        if not customer_ids:
            return

        open_rows = list(
            SubscriptionPeriod.objects.filter(
                organization=organization,
                subscription__customer_id__in=customer_ids,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            ).values_list("subscription__customer_id", "subscription__site_id")
        )
        open_customer_ids = {customer_id for customer_id, _site_id in open_rows}
        open_site_ids = {site_id for _customer_id, site_id in open_rows if site_id is not None}
        visible_invoices = sales_document_queryset_for(
            self.request.user,
            organization,
            membership=self.request.membership,
        ).filter(
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id__in=customer_ids,
        ).only("id", "customer_id", "number", "status", "issue_date", "created_at").order_by(
            "customer_id", "-issue_date", "-created_at", "-id"
        )
        latest_visible_invoice_by_customer = {}
        for invoice in visible_invoices:
            latest_visible_invoice_by_customer.setdefault(invoice.customer_id, invoice)

        today = timezone.localdate()
        for customer in customers:
            subscriptions = list(customer.subscriptions.all())
            self._attach_site_summary(customer, subscriptions, open_site_ids=open_site_ids)
            customer.primary_subscription = next(
                (
                    subscription
                    for subscription in subscriptions
                    if subscription.status == CustomerSubscription.Status.ACTIVE
                ),
                None,
            )
            customer.primary_service = customer.primary_subscription.internet_service if customer.primary_subscription else customer.primary_internet_service
            customer.whatsapp_url = self._whatsapp_link(customer.phone)
            customer.latest_visible_invoice = latest_visible_invoice_by_customer.get(customer.id)
            if customer.latest_visible_invoice is not None:
                customer.latest_visible_invoice_status = customer.latest_visible_invoice.get_status_display()

            paid_through_dates = [
                subscription.paid_through_date
                for subscription in subscriptions
                if subscription.paid_through_date is not None
            ]
            paid_through = max(paid_through_dates, default=None)
            if customer.id in open_customer_ids:
                customer.billing_state = "unpaid"
                customer.billing_label = "Invoice requires attention"
                customer.billing_note = "Payment status needs follow-up"
            elif paid_through is not None and paid_through < today:
                customer.billing_state = "unpaid"
                customer.billing_label = "Expired"
                customer.billing_note = f"Service paid through {paid_through:%b %d, %Y}"
            elif paid_through is not None and paid_through.month == today.month and paid_through.year == today.year:
                customer.billing_state = "due"
                customer.billing_label = "Due soon"
                customer.billing_note = f"Service paid through {paid_through:%b %d, %Y}"
            elif paid_through is not None:
                customer.billing_state = "paid"
                customer.billing_label = "Paid"
                customer.billing_note = f"Service paid through {paid_through:%b %d, %Y}"
            elif customer.primary_subscription:
                customer.billing_state = "due"
                customer.billing_label = "Not billed"
                customer.billing_note = "No payment status recorded"
            elif not customer.email and not customer.phone:
                customer.billing_state = "incomplete"
                customer.billing_label = "Incomplete"
                customer.billing_note = "Missing contact details"
            else:
                customer.billing_state = "neutral"
                customer.billing_label = "No subscription"
                customer.billing_note = "No active package billing"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        context['page_title'] = 'Customer List'
        base = Customer.objects.for_organization(organization)
        finance_all = has_tenant_permission(
            self.request.user, organization, PermissionCode.FINANCE_SALES_VIEW_ALL,
            membership=self.request.membership,
        )
        if finance_all:
            self._enrich_customers(context['customers'], organization)
        else:
            self._enrich_customers_for_sales(context['customers'], organization)

        unpaid_customer_ids = self._unpaid_customer_ids(organization)
        due_soon_customer_ids = self._due_soon_customer_ids(organization)
        no_contact = base.filter(
            (Q(email__isnull=True) | Q(email="")) &
            (Q(phone__isnull=True) | Q(phone=""))
        )
        context['total_customers'] = base.exclude(status=Customer.Status.SUSPENDED).count()
        context['customer_record_count'] = base.count()
        context['active_customers'] = base.active().count()
        context['inactive_customers'] = base.inactive().count()
        context['suspended_customers'] = base.suspended().count()
        if finance_all:
            context['overdue_customers'] = base.filter(id__in=unpaid_customer_ids).distinct().count()
            context['due_soon_customers'] = base.filter(id__in=due_soon_customer_ids).distinct().count()
        context['no_contact_customers'] = no_contact.count()
        context['today_customers'] = len(self._worked_today_customer_ids(organization, self.request.user))
        if finance_all:
            selected_month = self._selected_billing_month()
            selected_periods = self._billing_periods_for_selected_month(organization)
            context['selected_billing_month'] = selected_month
            context['selected_billing_month_value'] = selected_month.strftime('%Y-%m')
            context['selected_billing_month_label'] = selected_month.strftime('%B %Y')
            context['selected_paid_customers'] = selected_periods.filter(
                status=SubscriptionPeriod.Status.PAID,
            ).values('subscription__customer_id').distinct().count()
            context['selected_unpaid_customers'] = selected_periods.filter(
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            ).values('subscription__customer_id').distinct().count()
            context['total_open_receivables'] = (
                SubscriptionPeriod.objects.filter(
                    organization=organization,
                    status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
                ).aggregate(total=Sum("final_amount"))["total"]
                or 0
            )
            context['selected_month_open_receivables'] = (
                selected_periods.filter(
                    status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
                ).aggregate(total=Sum("final_amount"))["total"]
                or 0
            )
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        context["querystring"] = query_params.urlencode()
        context["active_worklist"] = self.request.GET.get("worklist", "")
        context["active_sort"] = getattr(self, "active_sort", self.request.GET.get("sort", "created"))
        context["active_filter_count"] = sum(
            bool(self.request.GET.get(key)) for key in ("type", "status")
        )
        context["finance_all"] = finance_all
        if context.get("page_obj"):
            context.update(page_context(self.request, context["page_obj"], page_size=self.get_paginate_by(self.object_list)))
        return context

# --- DetailView example ---
class CustomerDetailView(CustomFieldPageContextMixin, LoginRequiredMixin, DetailView):
    model = Customer
    template_name = 'customers/customer_detail.html'
    context_object_name = 'customer'
    custom_field_target_model = "customer"

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        return (
            Customer.objects.for_organization(organization)
            .select_related('internet_profile')
            .prefetch_related(
                'packages', 'sites__packages',
                'sites__internet_services__subscriptions__package',
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        customer = self.get_object()
        allowed_documents = sales_document_queryset_for(
            self.request.user, organization, membership=self.request.membership
        ).filter(customer=customer)
        finance_all = has_tenant_permission(
            self.request.user, organization, PermissionCode.FINANCE_SALES_VIEW_ALL,
            membership=self.request.membership,
        )
        billing_documents = list(
            allowed_documents
            .select_related("invoice", "corrected_invoice", "superseded_by")
            .order_by('-issue_date', '-created_at')[:10]
        )
        invoice_ids = [
            document.id
            for document in billing_documents
            if document.document_type == BillingDocument.DocumentType.INVOICE
        ]
        receipt_totals = {
            row["invoice_id"]: row["total"] or Decimal("0.00")
            for row in BillingDocument.objects.filter(
                organization=organization,
                document_type=BillingDocument.DocumentType.RECEIPT,
                invoice_id__in=invoice_ids,
            ).values("invoice_id").annotate(total=Sum("total"))
        }
        credit_totals = {
            row["corrected_invoice_id"]: -(row["total"] or Decimal("0.00"))
            for row in BillingDocument.objects.filter(
                organization=organization,
                document_type=BillingDocument.DocumentType.CREDIT_NOTE,
                status=BillingDocument.Status.ISSUED,
                corrected_invoice_id__in=invoice_ids,
            ).values("corrected_invoice_id").annotate(total=Sum("total"))
        }
        for document in billing_documents:
            if document.document_type == BillingDocument.DocumentType.INVOICE:
                document.timeline_paid = receipt_totals.get(document.id, Decimal("0.00"))
                document.timeline_credited = credit_totals.get(document.id, Decimal("0.00"))
                document.timeline_remaining = max(
                    document.total - document.timeline_paid - document.timeline_credited,
                    Decimal("0.00"),
                )
            elif document.document_type == BillingDocument.DocumentType.RECEIPT and document.invoice_id:
                document.timeline_reference = f"Payment applied to {document.invoice.number}"
            elif document.document_type == BillingDocument.DocumentType.CREDIT_NOTE and document.corrected_invoice_id:
                action = "Voided credit against" if document.status == BillingDocument.Status.VOID else "Credit applied against"
                document.timeline_reference = f"{action} {document.corrected_invoice.number}"
        context['billing_documents'] = billing_documents
        context['billing_document_count'] = allowed_documents.count()
        context['associated_assets'] = list(
            customer.external_assets.filter(organization=organization)
            .order_by('category_name', 'asset_tag', 'external_uuid')
        )
        context['subscriptions'] = (
            CustomerSubscription.objects.filter(
                organization=organization,
                customer=customer,
                status=CustomerSubscription.Status.ACTIVE,
            )
            .select_related("package", "promotion", "site", "internet_service")
            .prefetch_related("periods__invoice", "periods__invoice__items", "periods__receipt")
            .order_by("-site__is_primary", "site__name", "package__name", "-created_at")
        )
        subscriptions = list(context["subscriptions"])
        for subscription in subscriptions:
            latest_period, latest_amount = _subscription_billing_snapshot(subscription) if finance_all else (None, None)
            subscription.latest_period = latest_period
            subscription.latest_billing_amount = latest_amount
            subscription.latest_period_label = _period_label(latest_period)
            subscription.latest_paid_through = _subscription_paid_through_date(subscription)
            subscription.paid_coverage_windows = paid_service_coverage(subscription)
            subscription.latest_paid_coverage = (
                subscription.paid_coverage_windows[-1]
                if subscription.paid_coverage_windows else None
            )
        context["subscriptions"] = subscriptions
        context['subscription_periods'] = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                subscription__customer=customer,
                subscription__status=CustomerSubscription.Status.ACTIVE,
            )
            .select_related("subscription", "subscription__package", "subscription__site", "invoice", "receipt", "promotion")
            .filter(Q(invoice__isnull=True) | Q(invoice_id__in=allowed_documents.values('id')))
            .order_by("-period_start")[:8]
        )
        context['finance_all'] = finance_all
        context['packages'] = customer.packages.all()
        sites = list(
            customer.sites.all()
            .prefetch_related(
                "packages", "subscriptions__package",
                "internet_services__subscriptions__package",
            )
            .order_by("-is_primary", "name", "id")
        )
        service_count = 0
        operational_service_count = 0
        for site in sites:
            site.service_rows = list(site.internet_services.all())
            service_count += len(site.service_rows)
            for service in site.service_rows:
                service.subscription_rows = list(service.subscriptions.all())
                service.active_subscription = next(
                    (row for row in service.subscription_rows if row.status == CustomerSubscription.Status.ACTIVE),
                    None,
                )
                if service.operational_status == InternetService.OperationalStatus.ACTIVE:
                    operational_service_count += 1
        context['sites'] = sites
        context['service_count'] = service_count
        context['operational_service_count'] = operational_service_count
        context['can_manage_services'] = has_tenant_permission(
            self.request.user, organization, PermissionCode.CUSTOMERS_UPDATE,
            membership=self.request.membership,
        )
        context["custom_fields"] = CustomFieldService.get_custom_field_values(customer)
        context.update(self.get_custom_field_modal_context(target_model="customer"))
        try:
            internet_profile = customer.internet_profile
        except InternetCustomer.DoesNotExist:
            internet_profile = None
        context['internet_profile'] = _internet_profile_snapshot(customer, subscriptions, internet_profile)
        return context

# --- CreateView example ---
class CustomerCreateView(CustomFieldPageContextMixin, LoginRequiredMixin, CreateView):
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'
    custom_field_target_model = "customer"
    custom_field_inline_use = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'internet_form' in kwargs:
            context['internet_form'] = kwargs['internet_form']
            context.update(self.get_custom_field_modal_context(target_model="customer"))
            return context

        customer_type = None
        if self.request.method == 'POST':
            customer_type = self.request.POST.get('customer_type') or None
            context['internet_form'] = InternetCustomerForm(self.request.POST, customer_type=customer_type)
        else:
            context['internet_form'] = InternetCustomerForm(customer_type=customer_type)
        context.update(self.get_custom_field_modal_context(target_model="customer"))
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMER_CREATE)
        customer_type = form.cleaned_data.get('customer_type')

        internet_form = InternetCustomerForm(self.request.POST, customer_type=customer_type)
        if customer_type == 'internet' and not internet_form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, internet_form=internet_form))

        try:
            customer_instance = form.save(commit=False)
            internet_customer_instance = None
            if customer_type == "internet":
                internet_customer_instance = internet_form.save(commit=False)

            customer = CustomerService.create_customer_with_primary_service(
                organization=organization,
                actor=self.request.user,
                customer_instance=customer_instance,
                packages=form.cleaned_data.get("packages"),
                customer_type=customer_type,
                internet_profile_instance=internet_customer_instance,
                status_change_reason=form.cleaned_data.get("status_change_reason", ""),
                custom_field_data=form.cleaned_data,
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.render_to_response(self.get_context_data(form=form, internet_form=internet_form))

        self.object = customer
        messages.success(self.request, f'Customer {customer.name} created successfully.')
        return redirect(customer.get_absolute_url())

# --- UpdateView example ---
class CustomerUpdateView(CustomFieldPageContextMixin, LoginRequiredMixin, UpdateView):
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'
    custom_field_target_model = "customer"
    custom_field_inline_use = True

    def get_queryset(self):
        organization = require_organization(self.request)
        return Customer.objects.for_organization(organization)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        customer = self.get_object()
        if customer.customer_type != 'internet':
            if 'packages' in form.fields:
                form.fields['packages'].disabled = True
        if customer.status == Customer.Status.SUSPENDED:
            if 'packages' in form.fields:
                form.fields['packages'].disabled = True
                form.fields['packages'].help_text = 'Packages cannot be assigned to a suspended customer. Reactivate the customer first.'
        if customer.internet_services.exists():
            for field_name in ('customer_type', 'packages', 'ip_address', 'vlan_id'):
                if field_name in form.fields:
                    form.fields[field_name].disabled = True
            form.fields['packages'].help_text = 'Managed per Internet service from the customer workspace.'
        return form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    @staticmethod
    def _add_current_service_context(context, customer):
        """Use canonical agreement data in the account-edit summary."""
        subscription = (
            customer.subscriptions.filter(status=CustomerSubscription.Status.ACTIVE)
            .select_related("package", "site", "internet_service")
            .order_by("-site__is_primary", "start_date", "id")
            .first()
        )
        service = subscription.internet_service if subscription else (
            customer.internet_services.select_related("site")
            .exclude(operational_status=InternetService.OperationalStatus.DISCONNECTED)
            .order_by("-site__is_primary", "service_code", "id")
            .first()
        )
        context["current_service_subscription"] = subscription
        context["current_internet_service"] = service
        context["current_paid_coverage"] = None
        if subscription is not None:
            windows = paid_service_coverage(subscription)
            context["current_paid_coverage"] = windows[-1] if windows else None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer = self.get_object()

        if 'internet_form' in kwargs:
            context['internet_form'] = kwargs['internet_form']
            if customer.internet_services.exists():
                for field in context['internet_form'].fields.values():
                    field.disabled = True
                context['service_configuration_managed_separately'] = True
                self._add_current_service_context(context, customer)
            return context

        customer_type = customer.customer_type
        if self.request.method == 'POST':
            customer_type = self.request.POST.get('customer_type') or customer.customer_type

        internet_instance = None
        try:
            internet_instance = customer.internet_profile
        except InternetCustomer.DoesNotExist:
            internet_instance = None

        if self.request.method == 'POST':
            context['internet_form'] = InternetCustomerForm(self.request.POST, instance=internet_instance, customer_type=customer_type)
        else:
            context['internet_form'] = InternetCustomerForm(instance=internet_instance, customer_type=customer_type)

        if customer.internet_services.exists():
            for field in context['internet_form'].fields.values():
                field.disabled = True
            context['service_configuration_managed_separately'] = True
            self._add_current_service_context(context, customer)

        context.update(self.get_custom_field_modal_context(target_model="customer"))
        return context

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMERS_UPDATE)
        customer_type = form.cleaned_data.get('customer_type')

        internet_instance = None
        try:
            internet_instance = self.get_object().internet_profile
        except InternetCustomer.DoesNotExist:
            internet_instance = None

        internet_form = InternetCustomerForm(self.request.POST, instance=internet_instance, customer_type=customer_type)
        if self.get_object().internet_services.exists():
            for field in internet_form.fields.values():
                field.disabled = True
        if customer_type == 'internet' and not internet_form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, internet_form=internet_form))

        try:
            customer_instance = form.save(commit=False)
            internet_customer_instance = None
            if customer_type == "internet":
                internet_customer_instance = internet_form.save(commit=False)

            customer = CustomerService.upsert_customer(
                organization=organization,
                actor=self.request.user,
                customer_instance=customer_instance,
                packages=None if self.get_object().internet_services.exists() else form.cleaned_data.get("packages"),
                customer_type=customer_type,
                existing_internet_profile=internet_instance,
                internet_profile_instance=internet_customer_instance,
                status_change_reason=form.cleaned_data.get("status_change_reason", ""),
                custom_field_data=form.cleaned_data,
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.render_to_response(self.get_context_data(form=form, internet_form=internet_form))

        self.object = customer
        messages.success(self.request, f'Customer {customer.name} updated successfully.')
        return redirect(customer.get_absolute_url())


class CustomerSiteCreateView(LoginRequiredMixin, CreateView):
    model = CustomerSite
    form_class = CustomerSiteForm
    template_name = 'customers/customer_site_form.html'

    def get_customer(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMERS_UPDATE)
        return get_object_or_404(Customer.objects.for_organization(organization), pk=self.kwargs["customer_id"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field_name in ("ip_address", "vlan_id", "packages"):
            form.fields[field_name].disabled = True
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['customer'] = self.get_customer()
        context['page_title'] = f"Add site for {context['customer'].name}"
        return context

    def form_valid(self, form):
        organization = require_organization(self.request)
        customer = self.get_customer()
        site = form.save(commit=False)
        site.customer = customer

        try:
            site = CustomerService.upsert_site(
                organization=organization,
                actor=self.request.user,
                site_instance=site,
                packages=None,
                status_change_reason="",
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        self.object = site
        messages.success(self.request, f"Site {site.name} added successfully.")
        return redirect(customer.get_absolute_url())


class CustomerSiteUpdateView(LoginRequiredMixin, UpdateView):
    model = CustomerSite
    form_class = CustomerSiteForm
    template_name = 'customers/customer_site_form.html'

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMERS_UPDATE)
        return CustomerSite.objects.filter(organization=organization).select_related("customer")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field_name in ("ip_address", "vlan_id", "packages"):
            form.fields[field_name].disabled = True
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['customer'] = self.get_object().customer
        context['page_title'] = f"Edit site {self.object.name}"
        return context

    def form_valid(self, form):
        organization = require_organization(self.request)
        site = form.save(commit=False)
        try:
            site = CustomerService.upsert_site(
                organization=organization,
                actor=self.request.user,
                site_instance=site,
                packages=None,
                status_change_reason="",
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        self.object = site
        messages.success(self.request, f"Site {site.name} updated successfully.")
        return redirect(site.customer.get_absolute_url())


class InternetServiceCreateView(LoginRequiredMixin, FormView):
    template_name = "customers/internet_service_form.html"
    form_class = InternetServiceCreateForm

    def dispatch(self, request, *args, **kwargs):
        self.organization = require_organization(request)
        require_permission(request, PermissionCode.CUSTOMERS_UPDATE)
        self.customer = get_object_or_404(
            Customer.objects.for_organization(self.organization),
            pk=kwargs["customer_id"], customer_type="internet",
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(organization=self.organization, customer=self.customer)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["customer"] = self.customer
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            with transaction.atomic():
                service = InternetServiceDomainService.add_internet_service(
                    organization=self.organization,
                    actor=self.request.user,
                    customer_id=self.customer.id,
                    site_id=data["site"].id,
                    service_code=data.get("service_code"),
                    name=data["name"],
                    ip_address=data.get("ip_address"),
                    vlan_id=data.get("vlan_id"),
                    installed_at=data.get("installed_at"),
                    technical_notes=data.get("technical_notes", ""),
                )
                if data.get("package"):
                    InternetServiceDomainService.assign_initial_subscription(
                        organization=self.organization,
                        actor=self.request.user,
                        service_id=service.id,
                        package_id=data["package"].id,
                        start_date=data["subscription_start_date"],
                    )
        except CustomerServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, f"Internet service {service.service_code} created.")
        return redirect("internet-service-detail", pk=service.id)


class InternetServiceDetailView(LoginRequiredMixin, DetailView):
    model = InternetService
    template_name = "customers/internet_service_detail.html"
    context_object_name = "internet_service"

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        return InternetService.objects.filter(tenant=organization).select_related(
            "customer", "site"
        ).prefetch_related("subscriptions__package", "subscriptions__periods")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        history = list(
            self.object.subscriptions.select_related("package")
            .prefetch_related("periods")
            .order_by("-start_date", "-id")
        )
        for subscription in history:
            subscription.paid_coverage_windows = paid_service_coverage(subscription)
        context["subscription_history"] = history
        context["current_subscription"] = next(
            (row for row in history if row.status == CustomerSubscription.Status.ACTIVE), None,
        )
        context["paid_coverage_windows"] = paid_service_coverage_for_subscriptions(history)
        context["latest_paid_coverage"] = (
            context["paid_coverage_windows"][-1]
            if context["paid_coverage_windows"] else None
        )
        context["can_manage_service"] = has_tenant_permission(
            self.request.user, organization, PermissionCode.CUSTOMERS_UPDATE,
            membership=self.request.membership,
        )
        return context


class ServicePackageChangeView(LoginRequiredMixin, FormView):
    template_name = "customers/service_package_change.html"
    form_class = ServicePackageChangeForm

    def dispatch(self, request, *args, **kwargs):
        self.organization = require_organization(request)
        require_permission(request, PermissionCode.CUSTOMERS_UPDATE)
        self.service = get_object_or_404(
            InternetService.objects.filter(tenant=self.organization).select_related("customer", "site"),
            pk=kwargs["pk"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(organization=self.organization, service=self.service)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["internet_service"] = self.service
        context["current_subscription"] = self.service.current_subscription
        return context

    def form_valid(self, form):
        try:
            replacement = InternetServiceDomainService.change_service_package(
                organization=self.organization,
                actor=self.request.user,
                service_id=self.service.id,
                package_id=form.cleaned_data["package"].id,
                effective_date=form.cleaned_data["effective_date"],
                reason=form.cleaned_data["reason"],
            )
        except CustomerServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, f"Package changed to {replacement.package.name}; prior subscription retained in history.")
        return redirect("internet-service-detail", pk=self.service.id)


class ServiceStatusChangeView(LoginRequiredMixin, FormView):
    template_name = "customers/service_status_change.html"
    form_class = ServiceStatusChangeForm
    actions = {
        "block": ("Block service", InternetServiceDomainService.block_service),
        "unblock": ("Unblock service", InternetServiceDomainService.unblock_service),
        "disconnect": ("Disconnect service", InternetServiceDomainService.disconnect_service),
    }

    def dispatch(self, request, *args, **kwargs):
        self.organization = require_organization(request)
        require_permission(request, PermissionCode.CUSTOMERS_UPDATE)
        self.service = get_object_or_404(
            InternetService.objects.filter(tenant=self.organization).select_related("customer", "site"),
            pk=kwargs["pk"],
        )
        self.action = kwargs["action"]
        if self.action not in self.actions:
            return redirect("internet-service-detail", pk=self.service.id)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            internet_service=self.service,
            action=self.action,
            action_label=self.actions[self.action][0],
        )
        return context

    def form_valid(self, form):
        operation = self.actions[self.action][1]
        try:
            operation(
                organization=self.organization,
                actor=self.request.user,
                service_id=self.service.id,
                reason=form.cleaned_data["reason"],
            )
        except CustomerServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, f"{self.actions[self.action][0]} completed.")
        return redirect("internet-service-detail", pk=self.service.id)

# --- DeleteView example ---
class CustomerDeleteView(LoginRequiredMixin, DeleteView):
    model = Customer
    template_name = 'customers/customer_confirm_delete.html'
    success_url = reverse_lazy('customer-list')

    def get_queryset(self):
        organization = require_organization(self.request)
        return Customer.objects.for_organization(organization)

    def post(self, request, *args, **kwargs):
        customer = self.get_object()
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMER_ARCHIVE, obj=customer)
        try:
            CustomerService.soft_delete_customer(
                organization=organization,
                actor=self.request.user,
                customer_id=customer.id,
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return redirect(customer.get_absolute_url())

        messages.success(self.request, f'Customer {customer.name} archived successfully.')
        return redirect(self.success_url)


@login_required
@require_POST
def restore_customer(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.CUSTOMER_ARCHIVE)
    try:
        CustomerService.restore_customer(
            organization=organization,
            actor=request.user,
            customer_id=pk,
        )
    except CustomerServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Customer restored.")
    return redirect(reverse("customer-detail", args=[pk]))


class CustomerAnonymizeView(LoginRequiredMixin, FormView):
    template_name = "customers/customer_confirm_anonymize.html"
    form_class = AnonymizeCustomerForm

    def dispatch(self, request, *args, **kwargs):
        self.organization = require_organization(request)
        self.customer = get_object_or_404(Customer.all_objects, organization=self.organization, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["customer"] = self.customer
        return context

    def form_valid(self, form):
        require_permission(self.request, PermissionCode.CUSTOMER_ARCHIVE, obj=self.customer)
        try:
            CustomerService.anonymize_customer(
                organization=self.organization,
                actor=self.request.user,
                customer_id=self.customer.id,
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        messages.success(self.request, "Customer anonymized.")
        return redirect(self.customer.get_absolute_url())


class CustomerHardDeleteView(LoginRequiredMixin, FormView):
    template_name = "customers/customer_confirm_hard_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.organization = require_organization(request)
        self.customer = get_object_or_404(Customer.all_objects, organization=self.organization, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["customer_id"] = self.customer.id
        return kwargs

    def get_form_class(self):
        return HardDeleteCustomerForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["customer"] = self.customer
        return context

    def form_valid(self, form):
        require_permission(self.request, PermissionCode.CUSTOMER_ARCHIVE, obj=self.customer)
        try:
            CustomerService.hard_delete_customer(
                organization=self.organization,
                actor=self.request.user,
                customer_id=self.customer.id,
                confirm_phrase=form.cleaned_data["confirm_phrase"],
                confirm_one=form.cleaned_data["confirm_one"],
                confirm_two=form.cleaned_data["confirm_two"],
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        messages.success(self.request, "Customer permanently deleted.")
        return redirect(reverse("customer-list"))
