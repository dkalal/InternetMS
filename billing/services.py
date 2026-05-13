from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import calendar

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog
from customers.models import Customer
from products.models import Product
from services.models import Package
from users.models import Organization

from .models import BillingDocument, BillingLineItem, CustomerSubscription, Promotion, SubscriptionPeriod
from .numbering import DocumentNumberService


ISSUED_INVOICE_EDIT_ERROR = (
    "This invoice has already been issued. To modify it, create a credit note or cancel and reissue."
)
STANDARD_TAX_RATE = Decimal("18.00")
ZERO_TAX_RATE = Decimal("0.00")


@dataclass(frozen=True)
class LineItemInput:
    product_id: int | None = None
    package_id: int | None = None
    description: str = ""
    quantity: Decimal = Decimal("1.00")
    unit_price: Decimal = Decimal("0.00")
    base_unit_price: Decimal | None = None
    discount_amount: Decimal = Decimal("0.00")
    discount_percent: Decimal = Decimal("0.00")
    discount_reason: str = ""
    pricing_mode: str = BillingLineItem.PricingMode.RETAIL
    billing_behavior: str = BillingLineItem.BillingBehavior.ONE_TIME
    promotion_id: int | None = None


class BillingServiceError(Exception):
    code = "billing_error"


class BillingService:
    NON_EDITABLE_INVOICE_STATUSES = {
        BillingDocument.Status.SENT,
        BillingDocument.Status.ISSUED,
        BillingDocument.Status.PARTIALLY_PAID,
        BillingDocument.Status.PAID,
    }

    @classmethod
    def _raise_cross_tenant(cls):
        raise PermissionDenied("Cross-tenant object access denied.")

    @classmethod
    def _require_same_tenant(cls, organization: Organization, obj, *, attr: str = "organization_id"):
        if obj is None:
            return
        if getattr(obj, attr, None) != organization.id or getattr(obj, "tenant_id", organization.id) != organization.id:
            cls._raise_cross_tenant()

    @classmethod
    def _compute_totals(cls, *, tax_rate: Decimal, line_items: list[BillingLineItem]) -> tuple[Decimal, Decimal, Decimal]:
        subtotal = sum((li.line_total for li in line_items), Decimal("0.00"))
        tax_amount = (subtotal * (tax_rate / Decimal("100.00"))).quantize(Decimal("0.01"))
        total = (subtotal + tax_amount).quantize(Decimal("0.01"))
        return subtotal.quantize(Decimal("0.01")), tax_amount, total

    @classmethod
    def default_tax_rate_for_customer(cls, customer: Customer) -> Decimal:
        if (customer.vrn_number or "").strip():
            return STANDARD_TAX_RATE
        return ZERO_TAX_RATE

    @classmethod
    def _validate_editable_items(cls, *, items: list[LineItemInput]) -> None:
        if not items:
            raise BillingServiceError("At least one line item is required.")

        for item in items:
            quantity = item.quantity or Decimal("0.00")
            unit_price = item.unit_price or Decimal("0.00")
            if quantity <= Decimal("0.00"):
                raise BillingServiceError("Line item quantity must be greater than 0.")
            if unit_price < Decimal("0.00"):
                raise BillingServiceError("Line item unit price cannot be negative.")
            if item.discount_amount < Decimal("0.00"):
                raise BillingServiceError("Line item discount cannot be negative.")

    @classmethod
    def _promotion_discount(
        cls,
        *,
        promotion: Promotion,
        product: Product | None,
        package: Package | None,
        quantity: Decimal,
        unit_price: Decimal,
        gross: Decimal,
    ) -> tuple[Decimal, Decimal, str]:
        today = timezone.now().date()
        if not promotion.is_valid_for(when=today):
            raise BillingServiceError("Selected promotion is not active for today.")
        if promotion.minimum_quantity and quantity < promotion.minimum_quantity:
            raise BillingServiceError("Selected promotion requires a higher quantity.")
        if promotion.minimum_amount and gross < promotion.minimum_amount:
            raise BillingServiceError("Selected promotion requires a higher line amount.")
        if promotion.minimum_months and package is not None and quantity < Decimal(promotion.minimum_months):
            raise BillingServiceError("Selected promotion requires more subscription months.")

        if promotion.applies_to == Promotion.AppliesTo.PRODUCT and (
            product is None or promotion.product_id != product.id
        ):
            raise BillingServiceError("Selected promotion does not apply to this product.")
        if promotion.applies_to == Promotion.AppliesTo.PACKAGE and (
            package is None or promotion.package_id != package.id
        ):
            raise BillingServiceError("Selected promotion does not apply to this package.")

        discount = Decimal("0.00")
        price = unit_price
        if promotion.reward_type == Promotion.RewardType.PERCENT:
            discount = (gross * (promotion.reward_value / Decimal("100.00"))).quantize(Decimal("0.01"))
        elif promotion.reward_type == Promotion.RewardType.FIXED:
            discount = min(promotion.reward_value, gross).quantize(Decimal("0.01"))
        elif promotion.reward_type == Promotion.RewardType.WHOLESALE_PRICE:
            if product is None or not product.allow_wholesale or product.wholesale_price is None:
                raise BillingServiceError("Selected wholesale promotion is not available for this product.")
            price = product.wholesale_price.quantize(Decimal("0.01"))
        elif promotion.reward_type == Promotion.RewardType.FREE_MONTHS:
            discount = Decimal("0.00")
        return price, discount, promotion.name

    @classmethod
    def _serialize_items(cls, line_items) -> list[dict]:
        return [
            {
                "product_id": item.product_id,
                "package_id": item.package_id,
                "description": item.description,
                "quantity": str(item.quantity),
                "base_unit_price": str(item.base_unit_price),
                "unit_price": str(item.unit_price),
                "discount_amount": str(item.discount_amount),
                "pricing_mode": item.pricing_mode,
                "billing_behavior": item.billing_behavior,
                "promotion_id": item.promotion_id,
                "line_total": str(item.line_total),
            }
            for item in line_items
        ]

    @classmethod
    def _document_snapshot(cls, document: BillingDocument) -> dict:
        items = list(document.items.all().order_by("id"))
        return {
            "id": document.id,
            "document_type": document.document_type,
            "number": document.number,
            "status": document.status,
            "customer_id": document.customer_id,
            "issue_date": document.issue_date.isoformat() if document.issue_date else None,
            "due_date": document.due_date.isoformat() if document.due_date else None,
            "currency": document.currency,
            "tax_rate": str(document.tax_rate),
            "subtotal": str(document.subtotal),
            "tax_amount": str(document.tax_amount),
            "total": str(document.total),
            "notes": document.notes,
            "version_number": document.version_number,
            "parent_quotation_id": document.parent_quotation_id,
            "root_quotation_id": document.root_quotation_id,
            "is_current_version": document.is_current_version,
            "original_invoice_id": document.original_invoice_id,
            "corrected_invoice_id": document.corrected_invoice_id,
            "items": cls._serialize_items(items),
        }

    @classmethod
    def _log_action(
        cls,
        *,
        organization: Organization,
        performed_by,
        action_type: str,
        document: BillingDocument,
        old_value: dict | None = None,
        new_value: dict | None = None,
        metadata: dict | None = None,
    ) -> AuditLog:
        return AuditLog.objects.create(
            organization=organization,
            tenant=organization,
            actor=performed_by,
            performed_by=performed_by,
            action=action_type,
            action_type=action_type,
            object_type="BillingDocument",
            object_id=str(document.id),
            document_id=str(document.id),
            old_value=old_value or {},
            new_value=new_value or {},
            metadata=metadata or {},
            performed_at=timezone.now(),
        )

    @classmethod
    def _resolve_customer(cls, *, organization: Organization, customer_id: int) -> Customer:
        customer = Customer.all_objects.filter(id=customer_id).first()
        if customer is None:
            raise BillingServiceError("Invalid customer.")
        cls._require_same_tenant(organization, customer)
        if customer.status != Customer.Status.ACTIVE:
            raise BillingServiceError("Customer is not Active. Billing is not allowed.")
        return customer

    @classmethod
    def _resolve_line_item_refs(cls, *, organization: Organization, item: LineItemInput) -> tuple[Product | None, Package | None]:
        product = None
        package = None
        if item.product_id:
            product = Product.objects.unscoped().filter(id=item.product_id).first()
            if product is None:
                raise BillingServiceError("Invalid product.")
            cls._require_same_tenant(organization, product)
            if product.organization_id != organization.id:
                cls._raise_cross_tenant()
        if item.package_id:
            package = Package.objects.unscoped().filter(id=item.package_id).first()
            if package is None:
                raise BillingServiceError("Invalid package.")
            cls._require_same_tenant(organization, package)
            if package.organization_id != organization.id:
                cls._raise_cross_tenant()
        if product and package:
            raise BillingServiceError("Line item cannot reference both product and package.")
        if not product and not package and not item.description:
            raise BillingServiceError("Line item requires product, package, or description.")
        return product, package

    @classmethod
    def _build_line_items(
        cls,
        *,
        organization: Organization,
        document: BillingDocument,
        items: list[LineItemInput],
    ) -> list[BillingLineItem]:
        created_items: list[BillingLineItem] = []
        for item in items:
            product, package = cls._resolve_line_item_refs(organization=organization, item=item)
            promotion = None
            if item.promotion_id:
                promotion = Promotion.objects.unscoped().filter(id=item.promotion_id, organization=organization).first()
                if promotion is None:
                    raise BillingServiceError("Invalid promotion.")
            qty = (item.quantity or Decimal("0.00")).quantize(Decimal("0.01"))
            pricing_mode = item.pricing_mode
            unit = (item.unit_price or Decimal("0.00")).quantize(Decimal("0.01"))
            if pricing_mode != BillingLineItem.PricingMode.MANUAL:
                if product is not None:
                    wants_wholesale = pricing_mode == BillingLineItem.PricingMode.WHOLESALE or document.customer.pricing_tier in {
                        Customer.PricingTier.WHOLESALE,
                        Customer.PricingTier.CORPORATE,
                        Customer.PricingTier.VIP,
                    }
                    product_mode = Product.PricingMode.WHOLESALE if wants_wholesale else Product.PricingMode.RETAIL
                    unit = product.price_for(quantity=qty, pricing_mode=product_mode).quantize(Decimal("0.01"))
                    if product_mode == Product.PricingMode.WHOLESALE and unit == product.wholesale_price:
                        pricing_mode = BillingLineItem.PricingMode.WHOLESALE
                    else:
                        pricing_mode = BillingLineItem.PricingMode.RETAIL
                elif package is not None:
                    if item.billing_behavior == BillingLineItem.BillingBehavior.RECURRING_MONTHLY:
                        unit = package.monthly_fee.quantize(Decimal("0.01"))
                    else:
                        unit = package.price.quantize(Decimal("0.01"))
            base_unit = (item.base_unit_price if item.base_unit_price is not None else unit).quantize(Decimal("0.01"))
            discount_amount = (item.discount_amount or Decimal("0.00")).quantize(Decimal("0.01"))
            discount_reason = item.discount_reason
            if promotion is not None and pricing_mode != BillingLineItem.PricingMode.MANUAL:
                gross = (qty * unit).quantize(Decimal("0.01"))
                unit, promotion_discount, promotion_reason = cls._promotion_discount(
                    promotion=promotion,
                    product=product,
                    package=package,
                    quantity=qty,
                    unit_price=unit,
                    gross=gross,
                )
                discount_amount = promotion_discount
                discount_reason = discount_reason or promotion_reason
                pricing_mode = BillingLineItem.PricingMode.PROMOTION
            line_total = ((qty * unit) - discount_amount).quantize(Decimal("0.01"))
            if line_total < Decimal("0.00") and document.document_type != BillingDocument.DocumentType.CREDIT_NOTE:
                raise BillingServiceError("Line item discount cannot exceed line total.")
            created_items.append(
                BillingLineItem(
                    organization=organization,
                    tenant=organization,
                    document=document,
                    product=product,
                    package=package,
                    description=item.description,
                    quantity=qty,
                    base_unit_price=base_unit,
                    unit_price=unit,
                    discount_amount=discount_amount,
                    discount_percent=item.discount_percent or Decimal("0.00"),
                    discount_reason=discount_reason,
                    pricing_mode=pricing_mode,
                    billing_behavior=item.billing_behavior,
                    promotion=promotion,
                    line_total=line_total,
                )
            )
        return created_items

    @classmethod
    def _store_document(
        cls,
        *,
        organization: Organization,
        created_by,
        document_type: str,
        customer: Customer,
        issue_date: date,
        due_date: date | None,
        status: str,
        currency: str,
        tax_rate: Decimal,
        notes: str,
        items: list[LineItemInput],
        invoice: BillingDocument | None = None,
        original_invoice: BillingDocument | None = None,
        corrected_invoice: BillingDocument | None = None,
        payment_date: date | None = None,
        payment_method: str = "",
        payment_reference: str = "",
        number: str | None = None,
        version_number: int = 1,
        parent_quotation: BillingDocument | None = None,
        root_quotation: BillingDocument | None = None,
        is_current_version: bool = True,
    ) -> BillingDocument:
        if number is None:
            number = DocumentNumberService.next_number(
                organization=organization,
                document_type=document_type,
                issue_date=issue_date,
            ).value
        document = BillingDocument.objects.create(
            organization=organization,
            tenant=organization,
            document_type=document_type,
            number=number,
            customer=customer,
            issue_date=issue_date,
            issued_at=timezone.now(),
            due_date=due_date,
            status=status,
            currency=currency,
            tax_rate=tax_rate,
            notes=notes,
            created_by=created_by,
            invoice=invoice,
            original_invoice=original_invoice,
            corrected_invoice=corrected_invoice,
            payment_date=payment_date,
            payment_method=payment_method,
            payment_reference=payment_reference,
            version_number=version_number,
            parent_quotation=parent_quotation,
            root_quotation=root_quotation,
            is_current_version=is_current_version,
        )

        created_items = cls._build_line_items(organization=organization, document=document, items=items)
        if created_items:
            BillingLineItem.objects.bulk_create(created_items)

        subtotal, tax_amount, total = cls._compute_totals(tax_rate=tax_rate, line_items=created_items)
        BillingDocument.objects.filter(id=document.id).update(subtotal=subtotal, tax_amount=tax_amount, total=total)
        document.refresh_from_db()
        return document

    @classmethod
    def create_document(
        cls,
        *,
        organization: Organization,
        created_by,
        document_type: str,
        customer_id: int,
        issue_date: date | None = None,
        due_date: date | None = None,
        status: str = BillingDocument.Status.DRAFT,
        currency: str = "TZS",
        tax_rate: Decimal | None = None,
        notes: str = "",
        invoice_id: int | None = None,
        payment_date: date | None = None,
        payment_method: str = "",
        payment_reference: str = "",
        items: list[LineItemInput] | None = None,
    ) -> BillingDocument:
        if issue_date is None:
            issue_date = timezone.now().date()
        if items is None:
            items = []

        customer = cls._resolve_customer(organization=organization, customer_id=customer_id)
        if tax_rate is None:
            tax_rate = cls.default_tax_rate_for_customer(customer)
        invoice = None
        if document_type == BillingDocument.DocumentType.RECEIPT:
            if invoice_id is None:
                raise BillingServiceError("Receipt requires invoice.")
            invoice = BillingDocument.objects.unscoped().select_related("customer").prefetch_related("items").filter(
                id=invoice_id,
                document_type=BillingDocument.DocumentType.INVOICE,
            ).first()
            if invoice is None:
                raise BillingServiceError("Invalid invoice.")
            cls._require_same_tenant(organization, invoice)
            if invoice.organization_id != organization.id:
                cls._raise_cross_tenant()
        cls._validate_editable_items(items=items)

        with transaction.atomic():
            document = cls._store_document(
                organization=organization,
                created_by=created_by,
                document_type=document_type,
                customer=customer,
                issue_date=issue_date,
                due_date=due_date,
                status=status,
                currency=currency,
                tax_rate=tax_rate,
                notes=notes,
                items=items,
                invoice=invoice,
                payment_date=payment_date,
                payment_method=payment_method,
                payment_reference=payment_reference,
            )

            action_type = {
                BillingDocument.DocumentType.QUOTATION: "quotation_created",
                BillingDocument.DocumentType.INVOICE: "invoice_created",
                BillingDocument.DocumentType.CREDIT_NOTE: "credit_note_created",
            }.get(document_type)
            if action_type is not None:
                cls._log_action(
                    organization=organization,
                    performed_by=created_by,
                    action_type=action_type,
                    document=document,
                    new_value=cls._document_snapshot(document),
                )
            return document

    @classmethod
    def get_quotation_history(cls, *, organization: Organization, quotation_id: int):
        quotation = BillingDocument.objects.unscoped().filter(
            pk=quotation_id,
            document_type=BillingDocument.DocumentType.QUOTATION,
        ).first()
        if quotation is None:
            raise BillingServiceError("Quotation not found.")
        cls._require_same_tenant(organization, quotation)
        if quotation.organization_id != organization.id:
            cls._raise_cross_tenant()
        root_id = quotation.root_quotation_id or quotation.id
        return BillingDocument.objects.filter(
            organization=organization,
            document_type=BillingDocument.DocumentType.QUOTATION,
        ).filter(
            Q(root_quotation_id=root_id) | Q(id=root_id)
        )

    @classmethod
    def compare_quotation_versions(cls, *, organization: Organization, from_quotation_id: int, to_quotation_id: int) -> dict:
        versions = {
            item.id: item
            for item in BillingDocument.objects.unscoped().filter(
                document_type=BillingDocument.DocumentType.QUOTATION,
                id__in=[from_quotation_id, to_quotation_id],
            ).prefetch_related("items")
        }
        if len(versions) != 2:
            raise BillingServiceError("Quotation version not found.")
        left = versions[from_quotation_id]
        right = versions[to_quotation_id]
        cls._require_same_tenant(organization, left)
        cls._require_same_tenant(organization, right)
        left_snapshot = cls._document_snapshot(left)
        right_snapshot = cls._document_snapshot(right)
        diff = {}
        for key in ("issue_date", "due_date", "tax_rate", "subtotal", "tax_amount", "total", "notes"):
            if left_snapshot[key] != right_snapshot[key]:
                diff[key] = {"from": left_snapshot[key], "to": right_snapshot[key]}
        if left_snapshot["items"] != right_snapshot["items"]:
            diff["items"] = {"from": left_snapshot["items"], "to": right_snapshot["items"]}
        return {"from": left_snapshot, "to": right_snapshot, "changes": diff}

    @classmethod
    def create_quotation_version(
        cls,
        *,
        organization: Organization,
        created_by,
        quotation_id: int,
        customer_id: int,
        issue_date: date | None = None,
        due_date: date | None = None,
        status: str = BillingDocument.Status.DRAFT,
        currency: str = "TZS",
        tax_rate: Decimal = Decimal("18.00"),
        notes: str = "",
        items: list[LineItemInput] | None = None,
    ) -> BillingDocument:
        if issue_date is None:
            issue_date = timezone.now().date()
        if items is None:
            items = []
        previous = BillingDocument.objects.unscoped().filter(
            pk=quotation_id,
            document_type=BillingDocument.DocumentType.QUOTATION,
        ).prefetch_related("items").first()
        if previous is None:
            raise BillingServiceError("Quotation not found.")
        cls._require_same_tenant(organization, previous)
        if previous.organization_id != organization.id:
            cls._raise_cross_tenant()
        customer = cls._resolve_customer(organization=organization, customer_id=customer_id)
        cls._validate_editable_items(items=items)
        root = previous.root_quotation or previous
        with transaction.atomic():
            BillingDocument.objects.filter(pk=previous.pk).update(is_current_version=False)
            version = cls._store_document(
                organization=organization,
                created_by=created_by,
                document_type=BillingDocument.DocumentType.QUOTATION,
                customer=customer,
                issue_date=issue_date,
                due_date=due_date,
                status=status,
                currency=currency,
                tax_rate=tax_rate,
                notes=notes,
                items=items,
                number=previous.number,
                version_number=previous.version_number + 1,
                parent_quotation=previous,
                root_quotation=root,
                is_current_version=True,
            )
            cls._log_action(
                organization=organization,
                performed_by=created_by,
                action_type="quotation_version_created",
                document=version,
                old_value=cls._document_snapshot(previous),
                new_value=cls._document_snapshot(version),
                metadata={"parent_quotation_id": previous.id, "root_quotation_id": root.id},
            )
            return version

    @classmethod
    def update_draft_invoice(
        cls,
        *,
        organization: Organization,
        performed_by,
        invoice_id: int,
        tax_rate: Decimal,
        items: list[LineItemInput],
    ) -> BillingDocument:
        invoice = BillingDocument.objects.unscoped().filter(
            pk=invoice_id,
            document_type=BillingDocument.DocumentType.INVOICE,
        ).prefetch_related("items").first()
        if invoice is None:
            raise BillingServiceError("Invoice not found.")
        cls._require_same_tenant(organization, invoice)
        if invoice.organization_id != organization.id:
            cls._raise_cross_tenant()
        if invoice.status != BillingDocument.Status.DRAFT:
            raise BillingServiceError(ISSUED_INVOICE_EDIT_ERROR)
        cls._validate_editable_items(items=items)
        old_snapshot = cls._document_snapshot(invoice)
        with transaction.atomic():
            BillingLineItem.objects.filter(document=invoice).delete()
            created_items = cls._build_line_items(organization=organization, document=invoice, items=items)
            if created_items:
                BillingLineItem.objects.bulk_create(created_items)
            subtotal, tax_amount, total = cls._compute_totals(tax_rate=tax_rate, line_items=created_items)
            BillingDocument.objects.filter(pk=invoice.pk).update(
                tax_rate=tax_rate,
                subtotal=subtotal,
                tax_amount=tax_amount,
                total=total,
            )
            invoice.refresh_from_db()
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="invoice_edited",
                document=invoice,
                old_value=old_snapshot,
                new_value=cls._document_snapshot(invoice),
            )
            return invoice

    @classmethod
    def create_invoice_from_quotation(cls, *, organization: Organization, created_by, quotation_id: int) -> BillingDocument:
        quotation = BillingDocument.objects.unscoped().filter(
            id=quotation_id,
            document_type=BillingDocument.DocumentType.QUOTATION,
        ).prefetch_related("items").first()
        if quotation is None:
            raise BillingServiceError("Quotation not found.")
        cls._require_same_tenant(organization, quotation)
        if quotation.organization_id != organization.id:
            cls._raise_cross_tenant()
        if not quotation.is_current_version:
            raise BillingServiceError("Only the latest quotation version can be converted to an invoice.")

        items = [
            LineItemInput(
                product_id=item.product_id,
                package_id=item.package_id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                base_unit_price=item.base_unit_price,
                discount_amount=item.discount_amount,
                discount_percent=item.discount_percent,
                discount_reason=item.discount_reason,
                pricing_mode=item.pricing_mode,
                billing_behavior=item.billing_behavior,
                promotion_id=item.promotion_id,
            )
            for item in quotation.items.all()
        ]
        with transaction.atomic():
            invoice = cls.create_document(
                organization=organization,
                created_by=created_by,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=quotation.customer_id,
                issue_date=timezone.now().date(),
                due_date=quotation.due_date,
                status=BillingDocument.Status.DRAFT,
                currency=quotation.currency,
                tax_rate=quotation.tax_rate,
                notes=quotation.notes,
                items=items,
            )
            cls._log_action(
                organization=organization,
                performed_by=created_by,
                action_type="quotation_converted_to_invoice",
                document=invoice,
                old_value=cls._document_snapshot(quotation),
                new_value=cls._document_snapshot(invoice),
                metadata={"quotation_id": quotation.id, "quotation_version": quotation.version_number},
            )
            return invoice

    @classmethod
    def cancel_invoice(cls, *, organization: Organization, performed_by, invoice_id: int) -> BillingDocument:
        invoice = BillingDocument.objects.unscoped().filter(
            pk=invoice_id,
            document_type=BillingDocument.DocumentType.INVOICE,
        ).prefetch_related("items").first()
        if invoice is None:
            raise BillingServiceError("Invoice not found.")
        cls._require_same_tenant(organization, invoice)
        if invoice.organization_id != organization.id:
            cls._raise_cross_tenant()
        old_snapshot = cls._document_snapshot(invoice)
        with transaction.atomic():
            BillingDocument.objects.filter(pk=invoice.pk).update(status=BillingDocument.Status.CANCELLED)
            invoice.refresh_from_db()
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="invoice_cancelled",
                document=invoice,
                old_value=old_snapshot,
                new_value=cls._document_snapshot(invoice),
            )
            return invoice

    @classmethod
    def void_subscription_invoice(
        cls,
        *,
        organization: Organization,
        performed_by,
        period_id: int,
        reason: str,
    ) -> SubscriptionPeriod:
        reason = (reason or "").strip()
        if not reason:
            raise BillingServiceError("A reason is required to void a subscription invoice.")

        period = (
            SubscriptionPeriod.objects.unscoped()
            .select_related("subscription", "subscription__customer", "invoice", "receipt")
            .filter(pk=period_id, organization=organization)
            .first()
        )
        if period is None:
            raise BillingServiceError("Subscription period not found.")
        if period.invoice_id is None:
            raise BillingServiceError("This subscription period does not have an invoice to void.")
        if period.receipt_id is not None or period.status == SubscriptionPeriod.Status.PAID:
            raise BillingServiceError("Paid subscription periods need a credit note or payment reversal.")
        if period.status not in {SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE}:
            raise BillingServiceError("Only unpaid subscription invoices can be voided.")

        invoice = period.invoice
        cls._require_same_tenant(organization, invoice)
        if invoice.document_type != BillingDocument.DocumentType.INVOICE:
            raise BillingServiceError("Only invoices can be voided through this workflow.")
        if invoice.status == BillingDocument.Status.PAID:
            raise BillingServiceError("Paid invoices need a credit note or payment reversal.")
        if invoice.status in {BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED}:
            raise BillingServiceError("This invoice has already been resolved.")
        if invoice.receipts.filter(organization=organization).exists():
            raise BillingServiceError("This invoice already has a receipt.")

        old_invoice = cls._document_snapshot(invoice)
        old_period = {
            "id": period.id,
            "status": period.status,
            "invoice_id": period.invoice_id,
            "receipt_id": period.receipt_id,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
        }

        with transaction.atomic():
            BillingDocument.objects.filter(pk=invoice.pk).update(status=BillingDocument.Status.CANCELLED)
            SubscriptionPeriod.objects.filter(pk=period.pk).update(status=SubscriptionPeriod.Status.CANCELLED)
            invoice.refresh_from_db()
            period.refresh_from_db()

            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="subscription.invoice_voided",
                document=invoice,
                old_value=old_invoice,
                new_value=cls._document_snapshot(invoice),
                metadata={
                    "reason": reason,
                    "subscription_period_id": period.id,
                    "subscription_id": period.subscription_id,
                    "customer_id": period.subscription.customer_id,
                    "old_period": old_period,
                    "new_period": {
                        "id": period.id,
                        "status": period.status,
                        "invoice_id": period.invoice_id,
                        "receipt_id": period.receipt_id,
                    },
                },
            )
            return period

    @classmethod
    def reissue_invoice(
        cls,
        *,
        organization: Organization,
        performed_by,
        invoice_id: int,
        tax_rate: Decimal | None = None,
        reason: str = "",
    ) -> BillingDocument:
        invoice = BillingDocument.objects.unscoped().filter(
            pk=invoice_id,
            document_type=BillingDocument.DocumentType.INVOICE,
        ).prefetch_related("items").first()
        if invoice is None:
            raise BillingServiceError("Invoice not found.")
        cls._require_same_tenant(organization, invoice)
        if invoice.organization_id != organization.id:
            cls._raise_cross_tenant()
        if tax_rate is None:
            tax_rate = cls.default_tax_rate_for_customer(invoice.customer)
        items = [
            LineItemInput(
                product_id=item.product_id,
                package_id=item.package_id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                base_unit_price=item.base_unit_price,
                discount_amount=item.discount_amount,
                discount_percent=item.discount_percent,
                discount_reason=item.discount_reason,
                pricing_mode=item.pricing_mode,
                billing_behavior=item.billing_behavior,
                promotion_id=item.promotion_id,
            )
            for item in invoice.items.all()
        ]
        with transaction.atomic():
            original_snapshot = cls._document_snapshot(invoice)
            BillingDocument.objects.filter(pk=invoice.pk).update(status=BillingDocument.Status.CANCELLED)
            invoice.refresh_from_db()
            reissued = cls._store_document(
                organization=organization,
                created_by=performed_by,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer=invoice.customer,
                issue_date=timezone.now().date(),
                due_date=invoice.due_date,
                status=BillingDocument.Status.DRAFT,
                currency=invoice.currency,
                tax_rate=tax_rate,
                notes=invoice.notes,
                items=items,
                original_invoice=invoice,
            )
            SubscriptionPeriod.objects.filter(invoice=invoice, organization=organization).update(
                invoice=reissued,
                status=SubscriptionPeriod.Status.INVOICED,
            )
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="invoice_cancelled",
                document=invoice,
                old_value=original_snapshot,
                new_value=cls._document_snapshot(invoice),
            )
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="invoice_reissued",
                document=reissued,
                old_value=original_snapshot,
                new_value=cls._document_snapshot(reissued),
                metadata={"original_invoice_id": invoice.id, "reason": reason},
            )
            return reissued

    @classmethod
    def create_credit_note(
        cls,
        *,
        organization: Organization,
        performed_by,
        invoice_id: int,
        issue_date: date | None = None,
        notes: str = "",
    ) -> BillingDocument:
        if issue_date is None:
            issue_date = timezone.now().date()
        invoice = BillingDocument.objects.unscoped().filter(
            pk=invoice_id,
            document_type=BillingDocument.DocumentType.INVOICE,
        ).prefetch_related("items").first()
        if invoice is None:
            raise BillingServiceError("Invoice not found.")
        cls._require_same_tenant(organization, invoice)
        if invoice.organization_id != organization.id:
            cls._raise_cross_tenant()
        items = [
            LineItemInput(
                product_id=item.product_id,
                package_id=item.package_id,
                description=item.description,
                quantity=item.quantity * Decimal("-1.00"),
                unit_price=item.unit_price,
                base_unit_price=item.base_unit_price,
                discount_amount=item.discount_amount,
                discount_percent=item.discount_percent,
                discount_reason=item.discount_reason,
                pricing_mode=item.pricing_mode,
                billing_behavior=item.billing_behavior,
                promotion_id=item.promotion_id,
            )
            for item in invoice.items.all()
        ]
        with transaction.atomic():
            credit_note = cls._store_document(
                organization=organization,
                created_by=performed_by,
                document_type=BillingDocument.DocumentType.CREDIT_NOTE,
                customer=invoice.customer,
                issue_date=issue_date,
                due_date=None,
                status=BillingDocument.Status.ISSUED,
                currency=invoice.currency,
                tax_rate=invoice.tax_rate,
                notes=notes or f"Credit note for invoice {invoice.number}",
                items=items,
                corrected_invoice=invoice,
            )
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="credit_note_created",
                document=credit_note,
                old_value=cls._document_snapshot(invoice),
                new_value=cls._document_snapshot(credit_note),
                metadata={"corrected_invoice_id": invoice.id},
            )
            return credit_note

    @classmethod
    def create_receipt_from_invoice(
        cls,
        *,
        organization: Organization,
        created_by,
        invoice_id: int,
        payment_date: date | None = None,
        payment_method: str = "",
        payment_reference: str = "",
        notes: str = "",
    ) -> BillingDocument:
        invoice = BillingDocument.objects.unscoped().filter(
            id=invoice_id,
            document_type=BillingDocument.DocumentType.INVOICE,
        ).prefetch_related("items").first()
        if invoice is None:
            raise BillingServiceError("Invoice not found.")
        cls._require_same_tenant(organization, invoice)
        if invoice.organization_id != organization.id:
            cls._raise_cross_tenant()

        if payment_date is None:
            payment_date = timezone.now().date()

        payment_reference = (payment_reference or "").strip()

        items = [
            LineItemInput(
                product_id=item.product_id,
                package_id=item.package_id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                base_unit_price=item.base_unit_price,
                discount_amount=item.discount_amount,
                discount_percent=item.discount_percent,
                discount_reason=item.discount_reason,
                pricing_mode=item.pricing_mode,
                billing_behavior=item.billing_behavior,
                promotion_id=item.promotion_id,
            )
            for item in invoice.items.all()
        ]

        with transaction.atomic():
            existing_receipt = BillingDocument.objects.unscoped().filter(
                organization=organization,
                document_type=BillingDocument.DocumentType.RECEIPT,
                invoice=invoice,
            ).order_by("-created_at").first()
            if existing_receipt is not None:
                return existing_receipt

            if payment_reference:
                reference_owner = BillingDocument.objects.unscoped().filter(
                    organization=organization,
                    payment_reference=payment_reference,
                ).first()
                if reference_owner is not None:
                    if (
                        reference_owner.document_type == BillingDocument.DocumentType.RECEIPT
                        and reference_owner.invoice_id == invoice.id
                    ):
                        return reference_owner
                    raise BillingServiceError("This payment reference has already been used for another document.")

            try:
                receipt = cls._store_document(
                    organization=organization,
                    created_by=created_by,
                    document_type=BillingDocument.DocumentType.RECEIPT,
                    customer=invoice.customer,
                    issue_date=timezone.now().date(),
                    due_date=None,
                    status=BillingDocument.Status.PAID,
                    currency=invoice.currency,
                    tax_rate=invoice.tax_rate,
                    notes=notes,
                    items=items,
                    invoice=invoice,
                    payment_date=payment_date,
                    payment_method=payment_method,
                    payment_reference=payment_reference,
                )
            except IntegrityError as exc:
                if payment_reference:
                    raise BillingServiceError("This payment reference has already been used.") from exc
                raise
            new_status = BillingDocument.Status.PAID
            BillingDocument.objects.filter(id=invoice.id).update(status=new_status)
            invoice.refresh_from_db()
            linked_periods = list(
                SubscriptionPeriod.objects.filter(invoice=invoice, organization=organization).select_related("subscription")
            )
            SubscriptionPeriod.objects.filter(invoice=invoice, organization=organization).update(
                status=SubscriptionPeriod.Status.PAID,
                receipt=receipt,
                paid_at=timezone.now(),
            )
            for period in linked_periods:
                CustomerSubscription.objects.filter(id=period.subscription_id).update(
                    paid_through_date=period.period_end
                )

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=created_by,
                performed_by=created_by,
                action="billing.payment.registered",
                action_type="billing.payment.registered",
                object_type="BillingDocument",
                object_id=str(receipt.id),
                document_id=str(receipt.id),
                old_value={},
                new_value={"invoice_id": invoice.id, "payment_reference": payment_reference},
                metadata={"invoice_id": invoice.id, "payment_reference": payment_reference},
                performed_at=timezone.now(),
            )

            return receipt


def add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def first_day_of_month(value: date) -> date:
    return date(value.year, value.month, 1)


def last_day_of_month(value: date) -> date:
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


class SubscriptionBillingService:
    @classmethod
    def _raise_cross_tenant(cls):
        raise PermissionDenied("Cross-tenant object access denied.")

    @classmethod
    def get_or_create_subscription(
        cls,
        *,
        organization: Organization,
        customer: Customer,
        package: Package,
        start_date: date | None = None,
        promotion: Promotion | None = None,
    ) -> CustomerSubscription:
        if start_date is None:
            start_date = timezone.now().date()
        if customer.organization_id != organization.id or package.organization_id != organization.id:
            cls._raise_cross_tenant()
        subscription, _ = CustomerSubscription.objects.get_or_create(
            organization=organization,
            tenant=organization,
            customer=customer,
            package=package,
            status=CustomerSubscription.Status.ACTIVE,
            defaults={
                "start_date": start_date,
                "billing_day": start_date.day,
                "monthly_fee_at_signup": package.monthly_fee,
                "promotion": promotion,
            },
        )
        return subscription

    @classmethod
    def sync_customer_package_subscriptions(
        cls,
        *,
        organization: Organization,
        customer: Customer,
        start_date: date | None = None,
    ) -> list[CustomerSubscription]:
        subscriptions = []
        for package in customer.packages.filter(organization=organization, is_active=True):
            subscriptions.append(
                cls.get_or_create_subscription(
                    organization=organization,
                    customer=customer,
                    package=package,
                    start_date=start_date,
                )
            )
        return subscriptions

    @classmethod
    def best_package_promotion(
        cls,
        *,
        organization: Organization,
        package: Package,
        months: int,
        amount: Decimal,
        when: date,
    ) -> Promotion | None:
        candidates = Promotion.objects.filter(
            organization=organization,
            is_active=True,
            applies_to=Promotion.AppliesTo.PACKAGE,
        ).filter(Q(package=package) | Q(package__isnull=True))
        best = None
        best_value = Decimal("0.00")
        for promo in candidates:
            if not promo.is_valid_for(when=when):
                continue
            if months < promo.minimum_months or amount < promo.minimum_amount:
                continue
            value = Decimal("0.00")
            if promo.reward_type == Promotion.RewardType.PERCENT:
                value = (amount * (promo.reward_value / Decimal("100.00"))).quantize(Decimal("0.01"))
            elif promo.reward_type == Promotion.RewardType.FIXED:
                value = promo.reward_value
            elif promo.reward_type == Promotion.RewardType.FREE_MONTHS:
                value = package.monthly_fee * promo.reward_value
            if value > best_value:
                best = promo
                best_value = value
        return best

    @classmethod
    def calculate_period_amount(
        cls,
        *,
        subscription: CustomerSubscription,
        months: int,
        promotion: Promotion | None,
        when: date,
    ) -> dict:
        original = (subscription.monthly_fee_at_signup * Decimal(months)).quantize(Decimal("0.01"))
        discount = Decimal("0.00")
        free_months = 0
        if promotion and promotion.is_valid_for(when=when):
            if promotion.reward_type == Promotion.RewardType.PERCENT:
                discount = (original * (promotion.reward_value / Decimal("100.00"))).quantize(Decimal("0.01"))
            elif promotion.reward_type == Promotion.RewardType.FIXED:
                discount = min(promotion.reward_value, original).quantize(Decimal("0.01"))
            elif promotion.reward_type == Promotion.RewardType.FREE_MONTHS:
                free_months = int(promotion.reward_value)
        final = max(original - discount, Decimal("0.00")).quantize(Decimal("0.01"))
        return {"original": original, "discount": discount, "final": final, "free_months": free_months}

    @classmethod
    def create_period(
        cls,
        *,
        organization: Organization,
        subscription: CustomerSubscription,
        period_start: date,
        months: int = 1,
        promotion: Promotion | None = None,
    ) -> SubscriptionPeriod:
        if subscription.organization_id != organization.id:
            cls._raise_cross_tenant()
        period_start = first_day_of_month(period_start)
        amount = cls.calculate_period_amount(
            subscription=subscription,
            months=months,
            promotion=promotion,
            when=period_start,
        )
        paid_until_month = add_months(period_start, months + amount["free_months"] - 1)
        period_end = last_day_of_month(paid_until_month)
        period, _ = SubscriptionPeriod.objects.get_or_create(
            organization=organization,
            tenant=organization,
            subscription=subscription,
            period_start=period_start,
            defaults={
                "period_end": period_end,
                "months": months,
                "free_months": amount["free_months"],
                "original_amount": amount["original"],
                "discount_amount": amount["discount"],
                "final_amount": amount["final"],
                "promotion": promotion,
            },
        )
        return period

    @classmethod
    def create_invoice_for_period(
        cls,
        *,
        organization: Organization,
        created_by,
        period: SubscriptionPeriod,
        due_date: date | None = None,
    ) -> BillingDocument:
        if period.organization_id != organization.id:
            cls._raise_cross_tenant()
        if period.invoice_id:
            return period.invoice
        subscription = period.subscription
        description = (
            f"{subscription.package.name} subscription {period.period_start:%B %Y} "
            f"({period.months} month{'s' if period.months != 1 else ''})"
        )
        if period.free_months:
            description += f" + {period.free_months} free month{'s' if period.free_months != 1 else ''}"
        item = LineItemInput(
            package_id=subscription.package_id,
            description=description,
            quantity=Decimal(period.months),
            base_unit_price=subscription.monthly_fee_at_signup,
            unit_price=subscription.monthly_fee_at_signup,
            discount_amount=period.discount_amount,
            discount_reason=period.promotion.name if period.promotion_id else "",
            pricing_mode=BillingLineItem.PricingMode.PROMOTION if period.promotion_id else BillingLineItem.PricingMode.RETAIL,
            billing_behavior=BillingLineItem.BillingBehavior.RECURRING_MONTHLY,
            promotion_id=period.promotion_id,
        )
        with transaction.atomic():
            invoice = BillingService.create_document(
                organization=organization,
                created_by=created_by,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=subscription.customer_id,
                issue_date=timezone.now().date(),
                due_date=due_date,
                status=BillingDocument.Status.ISSUED,
                notes=f"Monthly subscription renewal for {period.period_start:%B %Y}.",
                items=[item],
            )
            SubscriptionPeriod.objects.filter(id=period.id).update(
                invoice=invoice,
                status=SubscriptionPeriod.Status.INVOICED,
            )
            period.refresh_from_db()
            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=created_by,
                performed_by=created_by,
                action="subscription.invoice_created",
                action_type="subscription.invoice_created",
                object_type="SubscriptionPeriod",
                object_id=str(period.id),
                document_id=str(invoice.id),
                metadata={"subscription_id": subscription.id, "invoice_id": invoice.id},
                performed_at=timezone.now(),
            )
            return invoice

    @classmethod
    def renew(
        cls,
        *,
        organization: Organization,
        created_by,
        subscription_id: int,
        period_start: date,
        months: int = 1,
        promotion_id: int | None = None,
        due_date: date | None = None,
        issue_invoice: bool = True,
    ) -> SubscriptionPeriod:
        subscription = CustomerSubscription.objects.unscoped().select_related("customer", "package").filter(
            id=subscription_id,
            organization=organization,
            status=CustomerSubscription.Status.ACTIVE,
        ).first()
        if subscription is None:
            raise BillingServiceError("Subscription not found.")
        if subscription.customer.status != Customer.Status.ACTIVE:
            raise BillingServiceError("Customer is not Active. Renewal is not allowed.")
        promotion = None
        if promotion_id:
            promotion = Promotion.objects.unscoped().filter(id=promotion_id, organization=organization).first()
            if promotion is None:
                raise BillingServiceError("Promotion not found.")
        if promotion is None:
            promotion = cls.best_package_promotion(
                organization=organization,
                package=subscription.package,
                months=months,
                amount=subscription.monthly_fee_at_signup * Decimal(months),
                when=period_start,
            )
        period = cls.create_period(
            organization=organization,
            subscription=subscription,
            period_start=period_start,
            months=months,
            promotion=promotion,
        )
        if issue_invoice:
            cls.create_invoice_for_period(
                organization=organization,
                created_by=created_by,
                period=period,
                due_date=due_date,
            )
            period.refresh_from_db()
        return period
