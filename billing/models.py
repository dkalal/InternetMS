from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from users.tenant_models import TenantScopedManager


class BillingDocument(models.Model):
    class DocumentType(models.TextChoices):
        QUOTATION = "quotation", "Quotation"
        INVOICE = "invoice", "Invoice"
        CREDIT_NOTE = "credit_note", "Credit Note"
        RECEIPT = "receipt", "Receipt"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        ISSUED = "issued", "Issued"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"
        REISSUED = "reissued", "Reissued"

    organization = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="billing_documents",
        db_index=True,
    )
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_billing_documents",
        null=True,
        blank=True,
        db_index=True,
    )
    document_type = models.CharField(max_length=20, choices=DocumentType.choices, db_index=True)
    number = models.CharField(max_length=60)

    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="billing_documents")

    issue_date = models.DateField(db_index=True)
    issued_at = models.DateTimeField(null=True, blank=True, db_index=True)
    due_date = models.DateField(blank=True, null=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    version_number = models.PositiveIntegerField(default=1)
    parent_quotation = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="child_quotation_versions",
        limit_choices_to={"document_type": DocumentType.QUOTATION},
    )
    root_quotation = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="quotation_version_history",
        limit_choices_to={"document_type": DocumentType.QUOTATION},
    )
    is_current_version = models.BooleanField(default=True, db_index=True)

    currency = models.CharField(max_length=10, default="TZS")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("18.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_billing_documents",
        null=True,
        blank=True,
    )

    invoice = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="receipts",
        limit_choices_to={"document_type": DocumentType.INVOICE},
    )
    original_invoice = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reissued_versions",
        limit_choices_to={"document_type": DocumentType.INVOICE},
    )
    corrected_invoice = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction_credit_notes",
        limit_choices_to={"document_type": DocumentType.INVOICE},
    )
    payment_date = models.DateField(blank=True, null=True)
    payment_method = models.CharField(max_length=50, blank=True, default="")
    payment_reference = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="Optional idempotency key (e.g., bank slip id, mobile money txn id).",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "document_type", "number", "version_number"],
                condition=models.Q(document_type="quotation"),
                name="uniq_quotation_version_number_per_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "document_type", "number"],
                condition=~models.Q(document_type="quotation"),
                name="uniq_non_quotation_number_per_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "payment_reference"],
                name="uniq_payment_reference_per_org",
                condition=~models.Q(payment_reference=""),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "document_type", "issue_date"]),
            models.Index(fields=["organization", "document_type", "status"]),
            models.Index(fields=["organization", "root_quotation", "version_number"]),
            models.Index(fields=["tenant", "document_type", "issue_date"]),
            models.Index(fields=["tenant", "status", "issue_date"]),
            models.Index(fields=["tenant", "payment_reference"]),
        ]
        ordering = ["-issue_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.get_document_type_display()} #{self.number}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.unscoped().filter(pk=self.pk).only("document_type", "status").first()
            if previous is not None and not getattr(self, "_allow_direct_edit", False):
                if previous.document_type == self.DocumentType.QUOTATION:
                    raise ValidationError("Quotation versions are immutable. Create a new version instead.")
                if previous.document_type == self.DocumentType.INVOICE and previous.status in {
                    self.Status.SENT,
                    self.Status.ISSUED,
                    self.Status.PARTIALLY_PAID,
                    self.Status.PAID,
                }:
                    raise ValidationError(
                        "This invoice has already been issued. To modify it, create a credit note or cancel and reissue."
                    )
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        super().save(*args, **kwargs)


class BillingLineItem(models.Model):
    class BillingBehavior(models.TextChoices):
        ONE_TIME = "one_time", "One time"
        RECURRING_MONTHLY = "recurring_monthly", "Recurring monthly"

    class PricingMode(models.TextChoices):
        RETAIL = "retail", "Retail"
        WHOLESALE = "wholesale", "Wholesale"
        PROMOTION = "promotion", "Promotion"
        MANUAL = "manual", "Manual"

    organization = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="billing_line_items",
        db_index=True,
    )
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_billing_line_items",
        null=True,
        blank=True,
        db_index=True,
    )
    document = models.ForeignKey(BillingDocument, on_delete=models.CASCADE, related_name="items")

    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, null=True, blank=True)
    package = models.ForeignKey("services.Package", on_delete=models.PROTECT, null=True, blank=True)
    description = models.TextField(blank=True, default="")

    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"))
    base_unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    discount_reason = models.CharField(max_length=160, blank=True, default="")
    pricing_mode = models.CharField(max_length=20, choices=PricingMode.choices, default=PricingMode.RETAIL)
    billing_behavior = models.CharField(max_length=30, choices=BillingBehavior.choices, default=BillingBehavior.ONE_TIME)
    promotion = models.ForeignKey(
        "Promotion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billing_line_items",
    )
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["organization", "document"]),
            models.Index(fields=["tenant", "document"]),
        ]

    def __str__(self) -> str:
        return f"Item for {self.document_id}"

    def save(self, *args, **kwargs):
        document = getattr(self, "document", None)
        if self.pk:
            document = type(self).objects.unscoped().select_related("document").get(pk=self.pk).document
        if document is not None and not getattr(self, "_allow_document_edit", False):
            if document.document_type == BillingDocument.DocumentType.QUOTATION:
                raise ValidationError("Quotation versions are immutable. Create a new version instead.")
            if document.document_type == BillingDocument.DocumentType.INVOICE and document.status in {
                BillingDocument.Status.SENT,
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PARTIALLY_PAID,
                BillingDocument.Status.PAID,
            }:
                raise ValidationError(
                    "This invoice has already been issued. To modify it, create a credit note or cancel and reissue."
                )
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        if self.base_unit_price == Decimal("0.00"):
            self.base_unit_price = self.unit_price
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        document = getattr(self, "document", None)
        if document is not None:
            if document.document_type == BillingDocument.DocumentType.QUOTATION:
                raise ValidationError("Quotation versions are immutable. Create a new version instead.")
            if document.document_type == BillingDocument.DocumentType.INVOICE and document.status in {
                BillingDocument.Status.SENT,
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PARTIALLY_PAID,
                BillingDocument.Status.PAID,
            }:
                raise ValidationError(
                    "This invoice has already been issued. To modify it, create a credit note or cancel and reissue."
                )
        return super().delete(*args, **kwargs)


class DocumentSequence(models.Model):
    class DocumentType(models.TextChoices):
        QUOTATION = BillingDocument.DocumentType.QUOTATION
        INVOICE = BillingDocument.DocumentType.INVOICE
        CREDIT_NOTE = BillingDocument.DocumentType.CREDIT_NOTE
        RECEIPT = BillingDocument.DocumentType.RECEIPT

    organization = models.ForeignKey("users.Organization", on_delete=models.PROTECT, related_name="billing_sequences")
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_billing_sequences",
        null=True,
        blank=True,
        db_index=True,
    )
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    sequence_date = models.DateField()
    last_number = models.PositiveIntegerField(default=0)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "document_type", "sequence_date"],
                name="uniq_billing_sequence_per_tenant_day",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "document_type", "sequence_date"]),
            models.Index(fields=["tenant", "document_type", "sequence_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.tenant_id}:{self.document_type}:{self.sequence_date}:{self.last_number}"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        super().save(*args, **kwargs)


class Promotion(models.Model):
    class AppliesTo(models.TextChoices):
        PACKAGE = "package", "Package"
        PRODUCT = "product", "Product"
        CART = "cart", "Cart"

    class RewardType(models.TextChoices):
        PERCENT = "percent", "Percentage discount"
        FIXED = "fixed", "Fixed amount discount"
        FREE_MONTHS = "free_months", "Free subscription months"
        WHOLESALE_PRICE = "wholesale_price", "Wholesale price"

    organization = models.ForeignKey("users.Organization", on_delete=models.PROTECT, related_name="promotions", db_index=True)
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_promotions",
        null=True,
        blank=True,
        db_index=True,
    )
    name = models.CharField(max_length=160)
    applies_to = models.CharField(max_length=20, choices=AppliesTo.choices)
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, null=True, blank=True, related_name="promotions")
    package = models.ForeignKey("services.Package", on_delete=models.PROTECT, null=True, blank=True, related_name="promotions")
    minimum_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"))
    minimum_months = models.PositiveIntegerField(default=1)
    minimum_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    reward_type = models.CharField(max_length=30, choices=RewardType.choices)
    reward_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["organization", "is_active", "applies_to"]),
            models.Index(fields=["tenant", "is_active", "applies_to"]),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        super().save(*args, **kwargs)

    def is_valid_for(self, *, when):
        if not self.is_active:
            return False
        if self.valid_from and when < self.valid_from:
            return False
        if self.valid_until and when > self.valid_until:
            return False
        return True


class CustomerSubscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey("users.Organization", on_delete=models.PROTECT, related_name="customer_subscriptions", db_index=True)
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_customer_subscriptions",
        null=True,
        blank=True,
        db_index=True,
    )
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="subscriptions")
    package = models.ForeignKey("services.Package", on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    billing_day = models.PositiveSmallIntegerField(default=1)
    monthly_fee_at_signup = models.DecimalField(max_digits=12, decimal_places=2)
    paid_through_date = models.DateField(null=True, blank=True)
    promotion = models.ForeignKey(Promotion, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscriptions")
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "customer", "package"],
                condition=models.Q(status="active"),
                name="uniq_active_subscription_per_customer_package",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["package", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.customer} - {self.package}"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        super().save(*args, **kwargs)


class SubscriptionPeriod(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INVOICED = "invoiced", "Invoiced"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        WAIVED = "waived", "Waived"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey("users.Organization", on_delete=models.PROTECT, related_name="subscription_periods", db_index=True)
    tenant = models.ForeignKey(
        "users.Organization",
        on_delete=models.PROTECT,
        related_name="tenant_subscription_periods",
        null=True,
        blank=True,
        db_index=True,
    )
    subscription = models.ForeignKey(CustomerSubscription, on_delete=models.PROTECT, related_name="periods")
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)
    months = models.PositiveIntegerField(default=1)
    free_months = models.PositiveIntegerField(default=0)
    original_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    final_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    invoice = models.ForeignKey(BillingDocument, on_delete=models.PROTECT, null=True, blank=True, related_name="subscription_periods")
    receipt = models.ForeignKey(BillingDocument, on_delete=models.PROTECT, null=True, blank=True, related_name="paid_subscription_periods")
    promotion = models.ForeignKey(Promotion, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscription_periods")
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["subscription", "period_start"], name="uniq_subscription_period_start")
        ]
        indexes = [
            models.Index(fields=["organization", "status", "period_start"]),
            models.Index(fields=["tenant", "status", "period_start"]),
            models.Index(fields=["invoice"]),
        ]

    def __str__(self) -> str:
        return f"{self.subscription_id}: {self.period_start} - {self.status}"

    def save(self, *args, **kwargs):
        if self.tenant_id is None and self.organization_id is not None:
            self.tenant_id = self.organization_id
        if self.organization_id is None and self.tenant_id is not None:
            self.organization_id = self.tenant_id
        if self.organization_id and self.tenant_id and self.organization_id != self.tenant_id:
            self.organization_id = self.tenant_id
        super().save(*args, **kwargs)
