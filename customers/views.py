from django.shortcuts import render, get_object_or_404, redirect
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from django.views.generic import TemplateView, ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views.generic.edit import FormView
from django.urls import reverse_lazy, reverse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.db.models import Exists, Max, OuterRef, Q, Subquery, Sum
from django.utils import timezone

from audit.models import AuditLog
from custom_fields.mixins import CustomFieldPageContextMixin
from billing.models import BillingDocument, BillingLineItem, CustomerSubscription, SubscriptionPeriod
from custom_fields.services import CustomFieldService
from billing.services import add_months, last_day_of_month

from .models import Customer, CustomerSite, InternetCustomer
from .forms import (
    CustomerForm,
    CustomerSiteForm,
    InternetCustomerForm,
    HardDeleteCustomerForm,
    AnonymizeCustomerForm,
)
from .services import CustomerService, CustomerServiceError
from users.permissions import PermissionCode, require_permission
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

    def _due_soon_customer_ids(self, organization):
        """Return customers with a paid, active service expiring later this month.

        This is a customer-level queue, so it uses the latest paid-through
        date across every active office/service. A customer who has already
        paid a later month for any active office must not be placed in Due
        soon just because another office ends earlier. A missing paid-through
        date means no payment has been recorded, and a date before today means
        the service is already expired. Customers with an open subscription
        invoice are kept in the unpaid worklist so queues never overlap.
        """
        today = timezone.localdate()
        month_end = today.replace(day=28)
        while True:
            next_day = month_end + timedelta(days=1)
            if next_day.month != month_end.month:
                break
            month_end = next_day
        open_subscription_invoices = SubscriptionPeriod.objects.filter(
            organization=organization,
            subscription__customer_id=OuterRef("customer_id"),
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
        )
        return (
            CustomerSubscription.objects.filter(
                organization=organization,
                status=CustomerSubscription.Status.ACTIVE,
            )
            .values("customer_id")
            .annotate(latest_paid_through=Max("paid_through_date"))
            .filter(
                latest_paid_through__gte=today,
                latest_paid_through__lte=month_end,
            )
            .exclude(Exists(open_subscription_invoices))
            .values("customer_id")
        )

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        queryset = (
            Customer.objects.for_organization(organization)
            .exclude(status=Customer.Status.SUSPENDED)
            .optimized_list()
            .prefetch_related("subscriptions__package", "subscriptions__periods", "subscriptions__periods__invoice__items")
        )
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
            queryset = queryset.filter(id__in=self._unpaid_customer_ids(organization))
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
                .prefetch_related("subscriptions__package", "subscriptions__periods", "subscriptions__periods__invoice__items")
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
                customer.subscriptions.select_related("site", "package")
                .prefetch_related("periods__invoice")
                .order_by("-site__is_primary", "site__name", "package__name", "id")
            )
            # Keep the primary-site subscription for the quick renew action.
            primary_subscriptions = [s for s in subscriptions if s.site and s.site.is_primary] or subscriptions
            customer.primary_subscription = primary_subscriptions[0] if primary_subscriptions else None
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        context['page_title'] = 'Customer List'
        base = Customer.objects.for_organization(organization)
        self._enrich_customers(context['customers'], organization)

        unpaid_customer_ids = self._unpaid_customer_ids(organization)
        due_soon_customer_ids = self._due_soon_customer_ids(organization)
        no_contact = base.filter(
            (Q(email__isnull=True) | Q(email="")) &
            (Q(phone__isnull=True) | Q(phone=""))
        )
        context['total_customers'] = base.exclude(status=Customer.Status.SUSPENDED).count()
        context['active_customers'] = base.active().count()
        context['inactive_customers'] = base.inactive().count()
        context['suspended_customers'] = base.suspended().count()
        context['overdue_customers'] = base.filter(id__in=unpaid_customer_ids).distinct().count()
        context['due_soon_customers'] = base.filter(id__in=due_soon_customer_ids).distinct().count()
        context['no_contact_customers'] = no_contact.count()
        context['today_customers'] = len(self._worked_today_customer_ids(organization, self.request.user))
        context['estimated_receivable'] = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            ).aggregate(total=Sum("final_amount"))["total"]
            or 0
        )
        today = timezone.localdate()
        context['monthly_receivable'] = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
                period_start__year=today.year,
                period_start__month=today.month,
            ).aggregate(total=Sum("final_amount"))["total"]
            or 0
        )
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        context["querystring"] = query_params.urlencode()
        context["active_worklist"] = self.request.GET.get("worklist", "")
        context["active_sort"] = getattr(self, "active_sort", self.request.GET.get("sort", "created"))
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
            .prefetch_related('packages', 'sites__packages')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        customer = self.get_object()
        context['billing_documents'] = (
            BillingDocument.objects.filter(organization=organization, customer=customer)
            .order_by('-issue_date', '-created_at')[:10]
        )
        context['subscriptions'] = (
            CustomerSubscription.objects.filter(
                organization=organization,
                customer=customer,
                status=CustomerSubscription.Status.ACTIVE,
            )
            .select_related("package", "promotion", "site")
            .prefetch_related("periods__invoice", "periods__invoice__items", "periods__receipt")
            .order_by("-site__is_primary", "site__name", "package__name", "-created_at")
        )
        subscriptions = list(context["subscriptions"])
        for subscription in subscriptions:
            latest_period, latest_amount = _subscription_billing_snapshot(subscription)
            subscription.latest_period = latest_period
            subscription.latest_billing_amount = latest_amount
            subscription.latest_period_label = _period_label(latest_period)
            subscription.latest_paid_through = _subscription_paid_through_date(subscription)
        context["subscriptions"] = subscriptions
        context['subscription_periods'] = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                subscription__customer=customer,
                subscription__status=CustomerSubscription.Status.ACTIVE,
            )
            .select_related("subscription", "subscription__package", "subscription__site", "invoice", "receipt", "promotion")
            .order_by("-period_start")[:8]
        )
        context['packages'] = customer.packages.all()
        context['sites'] = (
            customer.sites.filter(is_active=True)
            .prefetch_related("packages", "subscriptions__package")
            .order_by("-is_primary", "name", "id")
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

            customer = CustomerService.upsert_customer(
                organization=organization,
                actor=self.request.user,
                customer_instance=customer_instance,
                packages=form.cleaned_data.get("packages"),
                customer_type=customer_type,
                existing_internet_profile=None,
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
        return form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer = self.get_object()

        if 'internet_form' in kwargs:
            context['internet_form'] = kwargs['internet_form']
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

        context.update(self.get_custom_field_modal_context(target_model="customer"))
        return context

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMER_CREATE)
        customer_type = form.cleaned_data.get('customer_type')

        internet_instance = None
        try:
            internet_instance = self.get_object().internet_profile
        except InternetCustomer.DoesNotExist:
            internet_instance = None

        internet_form = InternetCustomerForm(self.request.POST, instance=internet_instance, customer_type=customer_type)
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
                packages=form.cleaned_data.get("packages"),
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
        require_permission(self.request, PermissionCode.CUSTOMER_CREATE)
        return get_object_or_404(Customer.objects.for_organization(organization), pk=self.kwargs["customer_id"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

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
                packages=form.cleaned_data.get("packages"),
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
        require_permission(self.request, PermissionCode.CUSTOMER_CREATE)
        return CustomerSite.objects.filter(organization=organization).select_related("customer")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

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
                packages=form.cleaned_data.get("packages"),
                status_change_reason="",
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        self.object = site
        messages.success(self.request, f"Site {site.name} updated successfully.")
        return redirect(site.customer.get_absolute_url())

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
