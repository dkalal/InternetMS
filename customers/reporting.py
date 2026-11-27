"""Read-only, tenant-scoped customer package change reporting."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms
from django.db.models import IntegerField, Q
from django.db.models.functions import Cast
from django.db.models.fields.json import KeyTextTransform, KeyTransform

from billing.models import CustomerSubscription, SubscriptionPeriod
from internetservices.tailwind import apply_tailwind
from services.models import Package


class PackageChangeFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=150, label="Search")
    date_from = forms.DateField(
        required=False, input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}), label="Effective from",
    )
    date_to = forms.DateField(
        required=False, input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}), label="Effective to",
    )
    package = forms.ModelChoiceField(
        required=False, queryset=Package.objects.none(), empty_label="All packages",
    )

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["package"].queryset = Package.objects.filter(tenant=organization).order_by("name", "id")
        self.fields["q"].widget.attrs["placeholder"] = "Customer, service, site or package"
        apply_tailwind(self)

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            self.add_error("date_to", "Effective to must be on or after Effective from.")
        return data


def package_change_events(*, organization, filters):
    """Return only this tenant's package-change audit events, newest first."""
    from audit.models import AuditLog

    events = AuditLog.objects.filter(
        tenant=organization,
        organization=organization,
        action="internet_service.package_changed",
        object_type="InternetService",
    ).annotate(
        report_effective_date=KeyTextTransform("effective_date", "metadata"),
        report_old_package_id=Cast(KeyTextTransform("package_id", "old_value"), IntegerField()),
        report_new_package_id=Cast(KeyTextTransform("package_id", "new_value"), IntegerField()),
        report_old_subscription_id=Cast(KeyTextTransform("subscription_id", "old_value"), IntegerField()),
        report_new_subscription_id=Cast(KeyTextTransform("subscription_id", "new_value"), IntegerField()),
        report_old_customer_name=KeyTextTransform("customer_name", KeyTransform("snapshot", "old_value")),
        report_old_site_name=KeyTextTransform("site_name", KeyTransform("snapshot", "old_value")),
        report_old_service_code=KeyTextTransform("service_code", KeyTransform("snapshot", "old_value")),
        report_old_service_name=KeyTextTransform("service_name", KeyTransform("snapshot", "old_value")),
        report_old_package_name=KeyTextTransform("package_name", KeyTransform("snapshot", "old_value")),
        report_new_customer_name=KeyTextTransform("customer_name", KeyTransform("snapshot", "new_value")),
        report_new_site_name=KeyTextTransform("site_name", KeyTransform("snapshot", "new_value")),
        report_new_service_code=KeyTextTransform("service_code", KeyTransform("snapshot", "new_value")),
        report_new_service_name=KeyTextTransform("service_name", KeyTransform("snapshot", "new_value")),
        report_new_package_name=KeyTextTransform("package_name", KeyTransform("snapshot", "new_value")),
    )
    if filters.get("date_from"):
        events = events.filter(report_effective_date__gte=filters["date_from"].isoformat())
    if filters.get("date_to"):
        events = events.filter(report_effective_date__lte=filters["date_to"].isoformat())
    if filters.get("package"):
        package_id = filters["package"].pk
        events = events.filter(Q(report_old_package_id=package_id) | Q(report_new_package_id=package_id))
    query = (filters.get("q") or "").strip()
    if query:
        # The current snapshot is searchable without loading the full event set.
        # Subscription IDs also cover pre-snapshot audit events whose catalog
        # labels have to be read as legacy values on the result page.
        matching_subscription_ids = (
            CustomerSubscription.objects.filter(tenant=organization)
            .filter(
                Q(customer__name__icontains=query)
                | Q(site__name__icontains=query)
                | Q(internet_service__service_code__icontains=query)
                | Q(internet_service__name__icontains=query)
                | Q(package__name__icontains=query)
            )
            .values("id")
        )
        snapshot_search = (
            Q(report_old_customer_name__icontains=query)
            | Q(report_old_site_name__icontains=query)
            | Q(report_old_service_code__icontains=query)
            | Q(report_old_service_name__icontains=query)
            | Q(report_old_package_name__icontains=query)
            | Q(report_new_customer_name__icontains=query)
            | Q(report_new_site_name__icontains=query)
            | Q(report_new_service_code__icontains=query)
            | Q(report_new_service_name__icontains=query)
            | Q(report_new_package_name__icontains=query)
        )
        legacy_search = Q(report_old_subscription_id__in=matching_subscription_ids) | Q(
            report_new_subscription_id__in=matching_subscription_ids
        )
        events = events.filter(snapshot_search | legacy_search)
    return events.select_related("actor", "performed_by").order_by("-performed_at", "-id")


def package_snapshot(*, service, subscription):
    """Capture reportable identity and agreed tariff at the event boundary."""
    package = subscription.package
    return {
        "subscription_id": subscription.pk,
        "package_id": package.pk,
        "package_name": package.name,
        "package_type": package.package_type,
        "speed": package.speed,
        "monthly_fee": str(Decimal(subscription.monthly_fee_at_signup).quantize(Decimal("0.01"))),
        "customer_id": service.customer_id,
        "customer_name": service.customer.name,
        "site_id": service.site_id,
        "site_name": service.site.name,
        "service_id": service.pk,
        "service_code": service.service_code,
        "service_name": service.name,
    }


def display_event(event, *, legacy_subscriptions, periods_by_subscription, localize):
    old_value = event.old_value or {}
    new_value = event.new_value or {}
    old_snapshot = old_value.get("snapshot")
    new_snapshot = new_value.get("snapshot")
    old_legacy = old_snapshot is None
    new_legacy = new_snapshot is None
    old_snapshot = old_snapshot or _legacy_snapshot(old_value, legacy_subscriptions)
    new_snapshot = new_snapshot or _legacy_snapshot(new_value, legacy_subscriptions)
    effective_date = _parse_date((event.metadata or {}).get("effective_date"))
    overlapping_periods = []
    old_subscription_id = old_value.get("subscription_id")
    if effective_date and old_subscription_id:
        overlapping_periods = [
            period for period in periods_by_subscription.get(old_subscription_id, [])
            if period.status != SubscriptionPeriod.Status.CANCELLED
            and period.period_start <= effective_date <= period.period_end
        ]
    recorded_at = event.performed_at or event.created_at
    old_fee = _parse_decimal(old_snapshot.get("monthly_fee"))
    new_fee = _parse_decimal(new_snapshot.get("monthly_fee"))
    return {
        "recorded_at": localize(recorded_at),
        "effective_date": effective_date,
        "customer_name": old_snapshot.get("customer_name") or new_snapshot.get("customer_name") or "Unavailable",
        "site_name": old_snapshot.get("site_name") or new_snapshot.get("site_name") or "Unavailable",
        "service_code": old_snapshot.get("service_code") or new_snapshot.get("service_code") or "Unavailable",
        "old_package": _package_label(old_snapshot),
        "new_package": _package_label(new_snapshot),
        "old_fee": old_snapshot.get("monthly_fee"),
        "new_fee": new_snapshot.get("monthly_fee"),
        "fee_delta": new_fee - old_fee if old_fee is not None and new_fee is not None else None,
        "reason": (event.metadata or {}).get("reason", ""),
        "actor": event.performed_by.get_full_name() or event.performed_by.username if event.performed_by_id else "System",
        "legacy": old_legacy or new_legacy,
        "overlapping_periods": overlapping_periods,
    }


def _legacy_snapshot(value, subscriptions):
    subscription = subscriptions.get(value.get("subscription_id"))
    if subscription is None:
        return {}
    package = subscription.package
    return {
        "customer_name": subscription.customer.name,
        "site_name": subscription.site.name if subscription.site_id else "Unavailable",
        "service_code": subscription.internet_service.service_code if subscription.internet_service_id else "Unavailable",
        "package_name": package.name,
        "speed": package.speed,
        "package_type": package.package_type,
        "monthly_fee": str(subscription.monthly_fee_at_signup),
    }


def _package_label(snapshot):
    name = snapshot.get("package_name") or "Unavailable"
    speed = snapshot.get("speed")
    return f"{name} · {speed}" if speed else name


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _parse_decimal(value):
    try:
        return Decimal(str(value)) if value is not None else None
    except (ArithmeticError, TypeError, ValueError):
        return None
