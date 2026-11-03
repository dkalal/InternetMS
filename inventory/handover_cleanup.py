"""Explicit-ID, offline maintenance. Never called from an HTTP operation.

The only unscoped reads are dependency probes tied to already authorized IDs:
they detect corrupt cross-tenant references rather than silently hiding them.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import date, datetime, time, timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import FileField, Q
from django.utils import timezone

from audit.models import AuditLog
from users.models import Organization, SupportAccessSession, TenantMembership


VERSION = 2
SALT = 'inventory.handover-cleanup.v2'
MAX_AGE = timedelta(hours=2)
# This is a closed policy, not a model-label/filter API.
INVENTORY = frozenset('inventory.' + name for name in (
    'supplier', 'supplierpaymentrecord', 'purchase', 'purchaseline',
    'purchaseproductproposal', 'purchaseimportsubmission', 'inventorybalance',
    'stockadjustment', 'stockmovement', 'stockunit', 'cart', 'cartline',
    'cartserialselection', 'inventorysale', 'inventorysaleline',
    'documentserialselection', 'importjob', 'historicalinventoryrecord',
)) | frozenset(('products.product', 'products.productcategory',
               'products.unitofmeasure', 'billing.billingdocument',
               'billing.billinglineitem', 'billing.promotion'))
POLICIES = {
    'inventory': INVENTORY,
    'customers': frozenset(('customers.customer',)),
    'sample_documents': frozenset(('billing.billingdocument', 'billing.billinglineitem', 'inventory.inventorysale')),
    'classified_inventory': INVENTORY,
}
CLASSIFIED_POLICIES = frozenset(('sample_documents', 'classified_inventory'))
CUSTOM_TARGETS = {'products.product': 'product', 'inventory.supplier': 'supplier',
                  'customers.customer': 'customer'}
PRESERVED = ['internet profiles, sites, assets, documents, packages and subscriptions',
             'subscription periods, billing sheets and unrelated financial history',
             'users, roles, settings, branding and custom-field definitions',
             'existing audit history, media files, PK and document/purchase sequences',
             'all records not explicitly approved (except exact target custom values)']


class CleanupError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def queryset(model):
    # _base_manager includes soft-deleted customers and bypasses request context.
    return model._base_manager.all()


def authorize(tenant_slug, actor_id, support_id):
    tenant = Organization.objects.get(slug=tenant_slug, is_active=True)
    actor = get_user_model().objects.get(pk=actor_id, is_active=True)
    if not TenantMembership.objects.filter(
        user=actor, is_active=True, tenant__is_active=True,
        base_role=TenantMembership.BaseRole.SUPER_ADMIN,
    ).exists():
        raise PermissionDenied('An active Super Administrator membership is required.')
    support = SupportAccessSession.objects.filter(
        pk=support_id, tenant=tenant, actor=actor, ended_at__isnull=True,
    ).first()
    if support is None or not support.reason.strip() or not support.session_key:
        raise PermissionDenied('An existing active, reasoned tenant support session is required.')
    return tenant, actor


def identity():
    try:
        commit = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        # Runtime images do not contain .git; never invent a deployed revision.
        commit = os.environ.get('HANDOVER_BUILD_COMMIT', '')
    if not commit:
        raise CleanupError('Build commit unavailable; provide HANDOVER_BUILD_COMMIT for the reviewed build.')
    if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        raise CleanupError('Build commit must be the full lowercase Git SHA.')
    # Include actual policy/model bytes, including dirty worktree edits.
    from pathlib import Path
    from django.conf import settings
    paths = set([Path(__file__)])
    for config in apps.get_app_configs():
        directory = Path(config.path)
        if directory.parent == Path(settings.BASE_DIR):
            paths.update(directory.rglob('*.py'))
    code = digest({str(p.relative_to(settings.BASE_DIR)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(paths)})
    executor = MigrationExecutor(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise CleanupError('Unapplied migrations; review and reconcile schema before planning.')
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid '
                           'JOIN pg_namespace n ON n.oid=c.relnamespace '
                           'WHERE NOT t.tgisinternal AND n.nspname=current_schema()')
            if cursor.fetchall():
                raise CleanupError('Unreviewed database triggers present; maintenance policy review required.')
            cursor.execute("SELECT conname FROM pg_constraint WHERE contype='f' "
                           "AND confdeltype NOT IN ('a','r') "
                           "AND connamespace=(SELECT oid FROM pg_namespace WHERE nspname=current_schema())")
            if cursor.fetchall():
                raise CleanupError('Database-level cascading/mutating FK actions present; manual schema review required.')
    return {'commit': commit, 'code': code, 'vendor': connection.vendor,
            'database': {k: str(connection.settings_dict.get(k, '')) for k in ('NAME', 'HOST', 'PORT')},
            'migrations': sorted([list(m) for m in MigrationRecorder(connection).applied_migrations()])}


def normalize(manifest, operation):
    if operation not in POLICIES or not isinstance(manifest, dict):
        raise CleanupError('Choose an allowlisted cleanup operation with an explicit ID manifest.')
    result = {}
    for label, ids in sorted(manifest.items()):
        if label not in POLICIES[operation]:
            raise CleanupError(f'Not allowed in {operation} plan: {label}')
        if not isinstance(ids, list) or any(type(pk) is not int or pk < 1 for pk in ids):
            raise CleanupError(f'{label}: IDs must be positive integers.')
        if ids:
            result[label] = sorted(set(ids))
    if not result:
        raise CleanupError('An explicit nonempty manifest is required; no automatic sample inference.')
    if sum(map(len, result.values())) > 10000:
        raise CleanupError('At most 10000 explicitly approved roots per plan.')
    return result


def normalize_classification(classification, manifest, operation):
    if operation not in CLASSIFIED_POLICIES:
        if classification is not None:
            raise CleanupError('Document classification requires an explicit classified operation.')
        return None
    if not isinstance(classification, dict) or set(classification) != {'document_ids', 'reason'}:
        raise CleanupError('Explicit sample document_ids and classification reason are required.')
    ids = classification['document_ids']
    reason = classification['reason']
    if not isinstance(ids, list) or not ids or any(type(pk) is not int or pk < 1 for pk in ids):
        raise CleanupError('Classified document IDs must be explicit positive integers.')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise CleanupError('A nonempty classification reason of at most 1000 characters is required.')
    ids = sorted(set(ids))
    selected = set(manifest.get('billing.billingdocument', []))
    if not set(ids) <= selected or (operation == 'sample_documents' and set(ids) != selected):
        raise CleanupError('Classification must match selected document roots; no outside IDs or implicit expansion.')
    return {'document_ids': ids, 'reason': reason.strip()}


def owned(row, tenant_id, seen=None):
    """Validate both legacy ownership columns and every tenant-owned FK endpoint."""
    seen = set() if seen is None else seen
    key = (row._meta.label_lower, row.pk)
    if key in seen:
        return True
    seen.add(key)
    fields = {f.name for f in row._meta.concrete_fields}
    direct = [getattr(row, f'{name}_id') for name in ('tenant', 'organization') if name in fields]
    if direct and any(value != tenant_id for value in direct):
        return False
    parents = []
    for field in row._meta.concrete_fields:
        if not field.is_relation or field.name in ('tenant', 'organization'):
            continue
        if getattr(row, field.attname) is None:
            continue
        parent_model = field.remote_field.model
        parent_fields = {f.name for f in parent_model._meta.concrete_fields}
        if {'tenant', 'organization'} & parent_fields:
            parent = queryset(parent_model).filter(pk=getattr(row, field.attname)).first()
            if parent is None or not owned(parent, tenant_id, seen):
                return False
            parents.append(parent)
    return bool(direct or parents)


def record(row):
    values = {}
    for field in row._meta.concrete_fields:
        value = field.value_from_object(row)
        # DjangoJSONEncoder truncates datetime microseconds to milliseconds.
        # Fingerprints must include every persisted digit, including timestamps.
        if isinstance(value, (date, datetime, time)):
            value = value.isoformat()
        elif isinstance(field, FileField):
            value = str(value)
        values[field.attname] = value
    return {'model': row._meta.label_lower, 'id': row.pk, 'content_hash': digest(values),
            'links': {f.name: getattr(row, f.attname) for f in row._meta.concrete_fields if f.is_relation}}


def resolve(manifest, operation, tenant, actor, classification=None):
    rows, blockers, dependencies, retained = {}, [], [], {}
    classified_ids = set((classification or {}).get('document_ids', []))

    def block(label, pk, reason):
        entry = {'model': label, 'id': pk, 'reason': reason}
        if entry not in blockers:
            blockers.append(entry)

    def add(row):
        label = row._meta.label_lower
        if not owned(row, tenant.pk):
            block(label, row.pk, 'Null, conflicting or cross-tenant ownership (including FK endpoints).')
            return
        rows[(label, row.pk)] = row

    for label, ids in manifest.items():
        model = apps.get_model(label)
        # Ownership constraints are explicit even with a request-scoped manager.
        selected = queryset(model).filter(pk__in=ids, tenant_id=tenant.pk)
        found = set()
        for row in selected:
            found.add(row.pk)
            add(row)
        for pk in sorted(set(ids) - found):
            block(label, pk, 'Missing or outside selected tenant; not authorized.')

    if operation == 'classified_inventory':
        # An independently added product must invalidate the plan, never expand it.
        products = apps.get_model('products.Product')
        catalog = set(queryset(products).filter(tenant=tenant).values_list('pk', flat=True))
        selected_products = set(manifest.get('products.product', []))
        for pk in sorted(catalog - selected_products):
            block('products.product', pk, 'Zero-products target requires this exact product ID to be reviewed; scope will not expand automatically.')
        for product in queryset(products).filter(Q(tenant=tenant) | Q(organization=tenant)).exclude(tenant=tenant, organization=tenant):
            block('products.product', product.pk, 'Conflicting/null catalog ownership prevents the zero-products target.')

    # Scalar custom values are the sole automatic children; definitions survive.
    values = apps.get_model('custom_fields.CustomFieldValue')
    for (label, pk), row in list(rows.items()):
        target = CUSTOM_TARGETS.get(label)
        if target:
            # A root-linked infrastructure probe deliberately detects foreign/null ownership.
            for value in queryset(values).filter(target_model=target, object_id=str(pk)):
                if value.field_definition.target_model != target:
                    block(value._meta.label_lower, value.pk, 'Custom-field definition/target mismatch; preserve for review.')
                add(value)

    keys = set(rows)
    all_models = list(apps.get_models(include_auto_created=True))
    for (label, pk), row in list(rows.items()):
        if label == 'inventory.supplier':
            jobs = apps.get_model('inventory.ImportJob')
            for job in queryset(jobs).filter(tenant=tenant):
                if any(isinstance(item, dict) and item.get('company_name') == row.company_name
                       for item in job.validated_rows):
                    if (job._meta.label_lower, job.pk) not in keys:
                        block(job._meta.label_lower, job.pk,
                              f'Import payload references supplier {pk} by company name; explicit approval required.')
        if label == 'inventory.stockunit':
            assets = apps.get_model('integrations.ExternalAssetReference')
            for asset in queryset(assets).filter(tenant=tenant, serial_number=row.serial_number):
                block(asset._meta.label_lower, asset.pk,
                      f'Assigned customer asset references selected serial unit {pk}; preserve stock/serial history.')
        if label == 'products.product' and row.sku:
            # Historical imports deliberately use SKU snapshots rather than FKs.
            history = apps.get_model('inventory.HistoricalInventoryRecord')
            jobs = apps.get_model('inventory.ImportJob')
            for historical in queryset(history).filter(tenant=tenant, sku=row.sku):
                if (historical._meta.label_lower, historical.pk) not in keys:
                    block(historical._meta.label_lower, historical.pk,
                          f'SKU snapshot references product {pk}; explicit approval required.')
            for job in queryset(jobs).filter(tenant=tenant):
                if any(isinstance(item, dict) and item.get('sku') == row.sku
                       for item in job.validated_rows):
                    if (job._meta.label_lower, job.pk) not in keys:
                        block(job._meta.label_lower, job.pk,
                              f'Import payload references product {pk} by SKU; explicit approval required.')
            proposals = apps.get_model('inventory.PurchaseProductProposal')
            for proposal in queryset(proposals).filter(tenant=tenant):
                if any(isinstance(item, dict) and item.get('sku') == row.sku for item in proposal.rows):
                    if (proposal._meta.label_lower, proposal.pk) not in keys:
                        block(proposal._meta.label_lower, proposal.pk,
                              f'Purchase proposal references product {pk} by SKU; explicit approval required.')
        snapshots = []
        if label == 'inventory.historicalinventoryrecord':
            snapshots = [{'sku': row.sku}]
        elif label == 'inventory.importjob':
            snapshots = row.validated_rows
        elif label == 'inventory.purchaseproductproposal':
            snapshots = row.rows
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                block(label, pk, 'Unrecognized historical/import payload; manual review required.')
                continue
            for model_label, attribute, value in (
                ('products.product', 'sku', snapshot.get('sku')),
                ('inventory.supplier', 'company_name', snapshot.get('company_name')),
            ):
                if value:
                    for endpoint in queryset(apps.get_model(model_label)).filter(tenant=tenant, **{attribute: value}):
                        if (model_label, endpoint.pk) not in keys:
                            block(model_label, endpoint.pk, f'Retained scalar endpoint of {label}:{pk}; partial sample-to-real history forbidden.')
        # Discover all incoming FK/O2O/M2M edges, including hidden '+' relations.
        for model in all_models:
            if model._meta.proxy:
                continue
            for field in model._meta.concrete_fields:
                if not field.is_relation or field.remote_field.model != type(row):
                    continue
                # This exact authorized-root probe must not hide inconsistent owners.
                for child in queryset(model).filter(**{field.attname: pk}):
                    child_key = (model._meta.label_lower, child.pk)
                    edge = {'from': list(child_key), 'field': field.name,
                            'to': [label, pk], 'on_delete': field.remote_field.on_delete.__name__}
                    dependencies.append(edge)
                    if child_key not in keys:
                        block(*child_key, f'References selected {label}:{pk} via {field.name}; explicit approval required, no cascade or detachment.')
                    if not owned(child, tenant.pk):
                        block(*child_key, 'Incoming reference has ambiguous or conflicting tenant ownership.')
        # Outgoing links fingerprint retained business endpoints as well.
        for field in row._meta.concrete_fields:
            if field.is_relation and getattr(row, field.attname) is not None:
                model = field.remote_field.model
                key = (model._meta.label_lower, getattr(row, field.attname))
                parent = queryset(model).filter(pk=key[1]).first()
                if parent is not None and key not in keys:
                    retained[key] = record(parent)
                # Financial chains and aggregate parents cannot be only partly approved.
                required = model._meta.label_lower in {
                    'products.product', 'inventory.purchase', 'inventory.cart',
                    'inventory.cartline', 'inventory.inventorysale',
                    'inventory.stockadjustment', 'inventory.purchaseline',
                    'inventory.stockunit', 'inventory.importjob',
                    'billing.billingdocument', 'billing.billinglineitem',
                }
                if operation == 'sample_documents' and model._meta.label_lower == 'products.product':
                    required = False  # Financial-only cleanup must retain the catalog and stock.
                if required and key not in keys:
                    block(*key, f'Retained aggregate endpoint of {label}:{pk} ({field.name}); partial history/stock cleanup forbidden.')
        if label == 'billing.billingdocument':
            customer = row.customer
            explicitly_sample = operation in CLASSIFIED_POLICIES and pk in classified_ids
            if (row.site_id or not customer.is_pos_placeholder) and not explicitly_sample:
                block(label, pk, 'Customer/site billing history protected; only explicitly approved walk-in POS chains supported.')
            if operation == 'sample_documents':
                allowed = (row.document_type == 'invoice' and row.status == 'draft') or (
                    row.document_type == 'quotation' and row.status in ('draft', 'sent'))
                # BillingService stamps issued_at on construction, even for drafts.
                # Lifecycle status plus receipt/stock dependencies are authoritative.
                if not allowed or row.payment_date or row.payment_reference or row.payment_method:
                    block(label, pk, 'Separate sample-document policy allows only unpaid draft invoices and draft/sent quotations.')
                if any(getattr(row, f'{name}_id') for name in (
                    'invoice', 'original_invoice', 'superseded_by', 'corrected_invoice', 'source_quotation', 'converted_invoice',
                )):
                    block(label, pk, 'Payment/correction/conversion chains require the complete inventory classification policy, not financial-only cleanup.')
        if label == 'billing.billinglineitem':
            document = row.document
            summary = (document.document_type in ('receipt', 'credit_note')
                       and document.invoice_id
                       and ('billing.billingdocument', document.invoice_id) in keys
                       and document.invoice.customer_id == document.customer_id)
            explicitly_sample = operation in CLASSIFIED_POLICIES and row.document_id in classified_ids
            mixed = bool(row.package_id or (not row.product_id and not summary))
            mixed_state_allowed = (document.document_type == 'invoice' and document.status == 'draft') or (
                document.document_type == 'quotation' and document.status in ('draft', 'sent'))
            mixed_state_allowed = mixed_state_allowed and not any((
                document.payment_date, document.payment_reference, document.payment_method,
            ))
            if row.subscription_id or row.internet_service_id:
                block(label, pk, 'Direct internet-service/subscription billing remains protected in every cleanup policy.')
            elif mixed and not (explicitly_sample and mixed_state_allowed):
                block(label, pk, 'Package, subscription, service or non-product billing is protected (mixed invoice).')
            if explicitly_sample and row.product_id and row.package_id:
                block(label, pk, 'Ambiguous line has both product and package ownership; preserve for review.')
        if operation == 'sample_documents' and label == 'inventory.inventorysale':
            if row.stock_deducted or row.receipt_id or row.completed_at or row.cart_id:
                block(label, pk, 'Financial-only cleanup requires a neutral sale with no posted stock, receipt, completion or cart.')
        if label == 'customers.customer':
            if row.is_pos_placeholder:
                block(label, pk, 'Generic walk-in customer is protected.')
            if not actor.is_superuser:
                block(label, pk, 'Existing customer hard-delete policy also requires is_superuser.')
            if AuditLog.objects.unscoped().filter(tenant=tenant, object_type='Customer', object_id=str(pk)).exists():
                block(label, pk, 'Existing customer hard-delete policy protects customers referenced by audit history.')
        if label == 'billing.billingdocument':
            logs = apps.get_model('messaging.WhatsAppManualMessageLog')
            # Scalar IDs have multiple historic type spellings; conservatively block any match.
            for log in queryset(logs).filter(tenant=tenant, related_object_id=str(pk)):
                block(log._meta.label_lower, log.pk, f'Messaging history references document {pk}; preserve and review.')

    affected = sorted((record(row) for row in rows.values()), key=lambda r: (r['model'], r['id']))
    return {'affected': affected, 'counts': dict(sorted(Counter(r['model'] for r in affected).items())),
            'dependencies': sorted(dependencies, key=canonical),
            'retained_endpoints': sorted(retained.values(), key=lambda r: (r['model'], r['id'])),
            'blockers': sorted(blockers, key=canonical),
            'other_mutations': ['Append handover.cleanup.applied audit event after exact deletion and constraint verification'],
            'media': 'retain all files'}


def make_plan(*, tenant_slug, actor_id, support_id, manifest, operation='inventory', created_at=None, classification=None):
    tenant, actor = authorize(tenant_slug, actor_id, support_id)
    approved = normalize(manifest, operation)
    classification = normalize_classification(classification, approved, operation)
    result = resolve(approved, operation, tenant, actor, classification)
    payload = {'version': VERSION, 'tenant': {'id': tenant.pk, 'slug': tenant.slug},
               'actor': actor.pk, 'support': support_id, 'operation': operation,
               'created_at': created_at or timezone.now().isoformat(), 'identity': identity(),
               'approved_roots': approved, 'classification': classification,
               'postconditions': {'products_remaining': 0} if operation == 'classified_inventory' else {},
               'preserved': PRESERVED, **result}
    fingerprint = digest(payload)
    return {'payload': payload, 'fingerprint': fingerprint,
            'signature': signing.Signer(salt=SALT).sign(fingerprint)}


def _delete_exact(payload, tenant):
    """Private maintenance exception: exact validated rows, no signals or media deletion.

    DELETE bypasses model immutability ONLY here. PostgreSQL constraints remain
    enabled. Child tables precede parents; same-table cycles use one statement.
    """
    grouped = {}
    for row in payload['affected']:
        grouped.setdefault(row['model'], []).append(row['id'])
    pending = set(grouped)
    with connection.cursor() as cursor:
        while pending:
            ready = []
            for label in sorted(pending):
                model = apps.get_model(label)
                # Delete referencing tables before referenced tables.
                if not any(
                    f.is_relation and f.remote_field.model == model
                    for other in pending - {label}
                    for f in apps.get_model(other)._meta.concrete_fields
                ):
                    ready.append(label)
            if not ready:
                raise CleanupError('Cross-table dependency cycle; no constraints will be disabled.')
            for label in ready:
                model = apps.get_model(label)
                ids = grouped[label]
                qn = connection.ops.quote_name
                fields = {f.name for f in model._meta.concrete_fields}
                if 'tenant' not in fields:
                    raise CleanupError(f'{label}: no reviewed tenant deletion path.')
                placeholders = ','.join(['%s'] * len(ids))
                cursor.execute(
                    f'DELETE FROM {qn(model._meta.db_table)} WHERE {qn(model._meta.pk.column)} '
                    f'IN ({placeholders}) AND {qn(model._meta.get_field("tenant").column)} = %s',
                    [*ids, tenant.pk],
                )
                if cursor.rowcount != len(ids):
                    raise CleanupError(f'{label}: unexpected deletion count; rollback required.')
                pending.remove(label)
        # Force deferred FK checks before recording success.
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def apply_plan(*, plan, tenant_slug, actor_id, support_id, fingerprint, confirmation,
               backup_reference, restore_reference, maintenance_reference, reason):
    # Authenticate before trusting any metadata or writing an audit event.
    tenant, actor = authorize(tenant_slug, actor_id, support_id)
    if connection.vendor != 'postgresql':
        raise CleanupError('Apply requires PostgreSQL; SQLite is planning-only.')
    if connection.in_atomic_block:
        raise CleanupError('Apply must own its transaction so failure audit survives rollback.')
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise CleanupError('A full SHA256 plan fingerprint is required.')
    if confirmation != f'CLEANUP {tenant.slug} {fingerprint}':
        raise CleanupError('Exact tenant-specific typed confirmation required.')
    evidence = {'backup': backup_reference, 'restore_rehearsal': restore_reference,
                'maintenance': maintenance_reference, 'reason': reason}
    if any(not isinstance(v, str) or not v.strip() or len(v) > 1000 for v in evidence.values()):
        raise CleanupError('Backup, restore rehearsal, write-quiescence evidence and reason required.')
    audit_context = {}
    try:
        payload = plan['payload']
        if signing.Signer(salt=SALT).unsign(plan['signature']) != digest(payload) or plan['fingerprint'] != fingerprint or digest(payload) != fingerprint:
            raise CleanupError('Plan integrity/fingerprint mismatch.')
        created = timezone.datetime.fromisoformat(payload['created_at'])
        age = timezone.now() - created
        if age < timedelta(0) or age > MAX_AGE:
            raise CleanupError('Plan expired or has a future timestamp; generate a fresh plan.')
        if payload['tenant'] != {'id': tenant.pk, 'slug': tenant.slug} or payload['actor'] != actor.pk or payload['support'] != support_id:
            raise CleanupError('Plan identity does not match tenant, actor and support session.')
        audit_context = {'operation': payload['operation'], 'classification': payload.get('classification')}
        with transaction.atomic(durable=True):
            # Unlike a command-only advisory lock, PostgreSQL table locks block
            # ordinary INSERT/UPDATE/DELETE, imports and background writers too.
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                tables = sorted(connection.introspection.table_names(cursor))
                qn = connection.ops.quote_name
                cursor.execute('LOCK TABLE ' + ','.join(qn(t) for t in tables) + ' IN SHARE ROW EXCLUSIVE MODE')
            fresh = make_plan(tenant_slug=tenant_slug, actor_id=actor_id, support_id=support_id,
                              manifest=payload['approved_roots'], operation=payload['operation'],
                              created_at=payload['created_at'], classification=payload.get('classification'))
            if fresh != plan:
                raise CleanupError('Stale plan: contents, dependencies, policy or database identity changed.')
            if payload['blockers']:
                raise CleanupError('Plan contains blockers; no deletion permitted.')
            _delete_exact(payload, tenant)
            if payload['operation'] == 'classified_inventory' and queryset(apps.get_model('products.Product')).filter(tenant=tenant).exists():
                raise CleanupError('Zero-products postcondition failed; rolling back the whole operation.')
            for expected in payload['retained_endpoints']:
                model = apps.get_model(expected['model'])
                endpoint = queryset(model).filter(pk=expected['id']).first()
                if endpoint is None or record(endpoint) != expected:
                    raise CleanupError('A retained endpoint changed; rolling back.')
            AuditLog.objects.create(tenant=tenant, organization=tenant, actor=actor,
                action='handover.cleanup.applied', object_type='HandoverPlan', object_id=fingerprint,
                metadata={**evidence, 'counts': payload['counts'], 'verification': 'exact counts and FK constraints checked',
                          'operation': payload['operation'], 'support': support_id,
                          'classification': payload.get('classification'), 'postconditions': payload['postconditions']})
        return payload['counts']
    except Exception as exc:
        # Outside the rollback transaction; no raw data, credentials or exception text.
        AuditLog.objects.create(tenant=tenant, organization=tenant, actor=actor,
            action='handover.cleanup.failed', object_type='HandoverPlan', object_id=fingerprint[:64],
            metadata={**evidence, **audit_context, 'error_type': type(exc).__name__, 'support': support_id})
        raise
