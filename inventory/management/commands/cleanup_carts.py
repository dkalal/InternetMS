from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from inventory.cart_lifecycle import cleanup_candidate
from inventory.models import Cart
from users.models import Organization


class Command(BaseCommand):
    help = 'Review cart cleanup; dry-run unless --apply. No history or business-linked cart is deleted.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--tenant', help='Tenant slug (omit for all tenants).')
        parser.add_argument('--batch-size', type=int, default=500)
        parser.add_argument('--after-id', type=int, default=0, help='Continue a reviewed bounded scan after this cart ID.')

    def handle(self, *args, **options):
        size = options['batch_size']
        if not 1 <= size <= 5000 or options['after_id'] < 0:
            raise CommandError('batch-size must be 1..5000; after-id must be non-negative.')
        now = timezone.now()
        qs = Cart.objects.unscoped().filter(status=Cart.Status.DRAFT, updated_at__lte=now - timedelta(hours=24), pk__gt=options['after_id'])
        if options['tenant']:
            tenant = Organization.objects.filter(slug=options['tenant']).first()
            if tenant is None:
                raise CommandError('Unknown tenant slug.')
            qs = qs.filter(tenant=tenant)
        ids = list(qs.order_by('pk').values_list('pk', flat=True)[:size])
        counts = Counter(examined=0, eligible=0, deleted=0, abandoned=0, skipped=0)
        reasons = Counter()
        for cart_id in ids:
            outcome, reason = cleanup_candidate(cart_id=cart_id, now=now, apply=options['apply'])
            self.stdout.write(f'cart_id={cart_id} outcome={outcome} reason={reason}')
            counts['examined'] += 1
            counts[outcome] += 1
            if outcome in {'deleted', 'abandoned'}:
                counts['eligible'] += 1
            reasons[reason] += 1
        self.stdout.write('mode=' + ('apply' if options['apply'] else 'dry-run'))
        self.stdout.write(' '.join(f'{key}={counts[key]}' for key in ('examined', 'eligible', 'deleted', 'abandoned', 'skipped')))
        self.stdout.write(' '.join(f'{key}={value}' for key, value in sorted(reasons.items())) or 'No candidates.')
        next_id = ids[-1] if ids and len(ids) == size else 0
        self.stdout.write(f'next_after_id={next_id} (0 means scan complete)')
