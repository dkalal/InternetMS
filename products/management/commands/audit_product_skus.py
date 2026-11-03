"""Tenant-only diagnostics and explicit, conservative blank-SKU backfill."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Upper, Trim
from products.creation import save_purchase_product
from products.models import Product
from users.models import Organization
from users.permissions import PermissionCode, has_tenant_permission


class Command(BaseCommand):
    help = 'Audit one tenant; --apply backfills unused blank SKUs only, with authorized actor and audit.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant-id', type=int, required=True)
        parser.add_argument('--actor-id', type=int)
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            tenant = Organization.objects.select_for_update().get(pk=options['tenant_id'])
        except Organization.DoesNotExist as exc:
            raise CommandError('Tenant not found.') from exc
        products = Product.objects.unscoped().filter(tenant=tenant)
        collisions = list(products.exclude(sku='').annotate(code=Upper(Trim('sku'))).values('code').annotate(
            count=Count('pk')).filter(count__gt=1))
        blanks = list(products.select_for_update().filter(sku='').order_by('pk'))
        protected = [p.pk for p in blanks if p.has_unit_history()]
        self.stdout.write(f'Tenant {tenant.pk}: products={products.count()}, blank={len(blanks)}, normalized_collision_groups={len(collisions)}')
        self.stdout.write(f'Blank product IDs: {[p.pk for p in blanks]}; history-dependent IDs: {protected}')
        if not options['apply']:
            self.stdout.write('DRY RUN: no products or counters changed.')
            return
        actor = get_user_model().objects.filter(pk=options['actor_id']).first()
        if actor is None or not has_tenant_permission(actor, tenant, PermissionCode.PRODUCTS_CREATE):
            raise CommandError('An actor with product creation permission in this tenant is required.')
        if collisions or protected:
            raise CommandError('Resolve normalized collisions and review history-dependent blanks before applying. No changes made.')
        for product in blanks:
            save_purchase_product(product, organization=tenant, actor=actor, _backfill=True)
        self.stdout.write(f'Backfilled {len(blanks)} unused blank SKUs; stock unchanged.')
