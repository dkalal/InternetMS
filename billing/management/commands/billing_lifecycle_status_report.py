from django.core.management.base import BaseCommand

from billing.models import BillingDocument
from users.models import Organization


class Command(BaseCommand):
    help = "List billing documents whose statuses do not fit the current invoice or quotation lifecycle."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Tenant ID or slug (required).")

    def handle(self, *args, **options):
        key = options["tenant"]
        tenant = Organization.objects.filter(pk=key).first() if str(key).isdigit() else None
        tenant = tenant or Organization.objects.filter(slug=key).first()
        if tenant is None:
            self.stderr.write(self.style.ERROR("Tenant not found."))
            return
        invoice_allowed = {
            BillingDocument.Status.DRAFT,
            BillingDocument.Status.ISSUED,
            BillingDocument.Status.PARTIALLY_PAID,
            BillingDocument.Status.PAID,
            BillingDocument.Status.VOID,
            BillingDocument.Status.SUPERSEDED,
            BillingDocument.Status.CANCELLED,
            BillingDocument.Status.REISSUED,
        }
        quotation_allowed = {
            BillingDocument.Status.DRAFT,
            BillingDocument.Status.SENT,
            BillingDocument.Status.ACCEPTED,
            BillingDocument.Status.APPROVED,
            BillingDocument.Status.REJECTED,
            BillingDocument.Status.EXPIRED,
            BillingDocument.Status.CONVERTED,
        }

        invoice_anomalies = BillingDocument.objects.unscoped().filter(
            tenant=tenant,
            document_type=BillingDocument.DocumentType.INVOICE,
        ).exclude(status__in=invoice_allowed)
        quotation_anomalies = BillingDocument.objects.unscoped().filter(
            tenant=tenant,
            document_type=BillingDocument.DocumentType.QUOTATION,
        ).exclude(status__in=quotation_allowed)

        if not invoice_anomalies.exists() and not quotation_anomalies.exists():
            self.stdout.write(self.style.SUCCESS("No lifecycle status anomalies found."))
            return

        if invoice_anomalies.exists():
            self.stdout.write("Invoice anomalies:")
            for document in invoice_anomalies.order_by("organization_id", "issue_date", "number"):
                self.stdout.write(
                    f"  invoice #{document.id} {document.number} org={document.organization_id} status={document.status}"
                )

        if quotation_anomalies.exists():
            self.stdout.write("Quotation anomalies:")
            for document in quotation_anomalies.order_by("organization_id", "issue_date", "number"):
                self.stdout.write(
                    f"  quotation #{document.id} {document.number} org={document.organization_id} status={document.status}"
                )
