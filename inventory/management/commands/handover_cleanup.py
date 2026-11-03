import json
from pathlib import Path

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.signing import BadSignature
from django.core.management.base import BaseCommand, CommandError

from inventory.handover_cleanup import CleanupError, POLICIES, apply_plan, make_plan


class Command(BaseCommand):
    help = 'Plan exact sample IDs (read-only by default), or apply an unchanged signed handover plan.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='Exact active tenant slug')
        parser.add_argument('--actor', required=True, type=int)
        parser.add_argument('--support-session', required=True, type=int)
        parser.add_argument('--operation', choices=tuple(POLICIES))
        parser.add_argument('--classification-file', help='JSON with explicit sample document_ids and reason; classified operations only')
        parser.add_argument('--manifest', help='JSON mapping of allowlisted model keys to approved integer IDs')
        parser.add_argument('--output', help='New plan JSON file; existing files are never overwritten')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--plan', help='Previously generated signed JSON plan')
        parser.add_argument('--fingerprint')
        parser.add_argument('--confirm')
        parser.add_argument('--backup-reference')
        parser.add_argument('--restore-reference')
        parser.add_argument('--maintenance-reference')
        parser.add_argument('--reason')

    def handle(self, **options):
        common = dict(tenant_slug=options['tenant'], actor_id=options['actor'],
                      support_id=options['support_session'])
        try:
            if options['apply']:
                if options['manifest'] or options['output'] or options['classification_file'] or not options['plan'] or not options['fingerprint']:
                    raise CleanupError('Apply requires --plan and --fingerprint, and forbids --manifest/--output/--classification-file.')
                plan = json.loads(Path(options['plan']).read_text(encoding='utf-8'))
                if options['operation'] and options['operation'] != plan['payload']['operation']:
                    raise CleanupError('Requested operation does not match the immutable signed plan.')
                counts = apply_plan(**common, plan=plan, fingerprint=options['fingerprint'],
                    confirmation=options['confirm'], backup_reference=options['backup_reference'],
                    restore_reference=options['restore_reference'],
                    maintenance_reference=options['maintenance_reference'], reason=options['reason'])
                self.stdout.write('Applied exact approved plan: ' + json.dumps(counts, sort_keys=True))
                return
            if options['plan'] or not options['manifest'] or not options['output']:
                raise CleanupError('Dry-run requires --manifest and --output; --plan is apply-only.')
            manifest = json.loads(Path(options['manifest']).read_text(encoding='utf-8'))
            classification = json.loads(Path(options['classification_file']).read_text(encoding='utf-8')) if options['classification_file'] else None
            plan = make_plan(**common, manifest=manifest, operation=options['operation'] or 'inventory', classification=classification)
            # Exclusive creation prevents accidental replacement of reviewed approval evidence.
            with Path(options['output']).open('x', encoding='utf-8') as handle:
                json.dump(plan, handle, indent=2, sort_keys=True)
                handle.write('\n')
            payload = plan['payload']
            self.stdout.write(f"DRY RUN: tenant={options['tenant']} actor={options['actor']} operation={payload['operation']}")
            self.stdout.write('Fingerprint: ' + plan['fingerprint'])
            for label, count in payload['counts'].items():
                ids = [str(r['id']) for r in payload['affected'] if r['model'] == label]
                self.stdout.write(f"DELETE {label}: count={count} IDs={','.join(ids)}")
            for blocker in payload['blockers']:
                self.stdout.write(f"BLOCK {blocker['model']}:{blocker['id']}: {blocker['reason']}")
            self.stdout.write('Preserve: ' + '; '.join(payload['preserved']))
            self.stdout.write(f"Plan saved: {options['output']}; blockers={len(payload['blockers'])}; database writes=0")
        except (CleanupError, BadSignature, PermissionDenied, ObjectDoesNotExist, OSError, ValueError,
                KeyError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
