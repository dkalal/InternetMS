from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim

from billing.models import BillingLineItem


class Command(BaseCommand):
    help = 'Read-only audit for billing-line unit snapshots that differ from the current product unit.'

    def add_arguments(self, parser):
        parser.add_argument('--fail-on-drift', action='store_true')

    def handle(self, *args, **options):
        lines = (
            BillingLineItem.objects.unscoped()
            .filter(product__isnull=False)
            .annotate(
                normalized_snapshot=Lower(Trim('unit_snapshot')),
                normalized_product_unit=Lower(Trim('product__measure_unit')),
            )
            .filter(Q(unit_factor__isnull=False) | ~Q(normalized_snapshot=F('normalized_product_unit')))
            .select_related('document', 'product', 'product__sales_unit', 'packaging__unit')
            .order_by('tenant_id', 'product_id', 'document_id', 'id')
        )
        rows = []
        for line in lines.iterator(chunk_size=500):
            if line.unit_factor is not None:
                from django.core.exceptions import ValidationError
                from products.packaging_snapshots import validate_snapshot
                try:
                    validate_snapshot(line)
                    if line.packaging_id and line.packaging.unit.tenant_id != line.tenant_id:
                        raise ValidationError('Commercial unit belongs to another tenant.')
                except ValidationError:
                    rows.append(line)
            else:
                rows.append(line)
        product_ids = sorted({line.product_id for line in rows})
        open_statuses = {'draft', 'sent', 'issued', 'partially_paid'}
        open_count = sum(
            line.document.document_type == 'invoice' and line.document.status in open_statuses
            for line in rows
        )
        self.stdout.write(
            f'unit_drift_lines={len(rows)} affected_products={len(product_ids)} open_invoice_lines={open_count}'
        )
        for line in rows:
            self.stdout.write(
                ' '.join((
                    f'tenant={line.tenant_id}',
                    f'product={line.product_id}',
                    f'document={line.document_id}',
                    f'number={line.document.number}',
                    f'status={line.document.status}',
                    f'snapshot_unit={line.unit_snapshot}',
                    f'current_unit={line.product.get_measure_unit_display()}',
                ))
            )
        if rows and options['fail_on_drift']:
            raise CommandError('Product unit drift detected; review the listed historical documents before deployment.')
        if not rows:
            self.stdout.write(self.style.SUCCESS('No product unit drift detected.'))
