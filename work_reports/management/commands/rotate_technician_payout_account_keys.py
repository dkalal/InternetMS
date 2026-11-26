from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from work_reports.bank_account_security import (
    PayoutAccountEncryptionUnavailable,
    rotate_payout_account_ciphertext,
)
from work_reports.models import TechnicianPayoutAccount


class Command(BaseCommand):
    help = "Re-encrypt Technician payout account numbers under the primary configured key."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persist ciphertext changes. Default is dry-run.")

    def handle(self, *args, **options):
        apply = options["apply"]
        rotated = 0
        unchanged = 0
        try:
            with transaction.atomic():
                accounts = TechnicianPayoutAccount.objects.unscoped().select_for_update().order_by("pk")
                for account in accounts.iterator(chunk_size=200):
                    ciphertext, needs_rotation = rotate_payout_account_ciphertext(
                        account.account_number_encrypted,
                    )
                    if not needs_rotation:
                        unchanged += 1
                        continue
                    rotated += 1
                    if apply:
                        account.account_number_encrypted = ciphertext
                        account._key_rotation = True
                        account.save(update_fields=["account_number_encrypted", "updated_at"])
                if not apply:
                    transaction.set_rollback(True)
        except PayoutAccountEncryptionUnavailable as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            # Do not include account data or ciphertext in command output.
            raise CommandError("Payout account key rotation failed; no changes were committed.") from exc

        mode = "Updated" if apply else "Dry run found"
        self.stdout.write(self.style.SUCCESS(
            f"{mode} {rotated} account ciphertext(s); {unchanged} already use the primary key."
        ))
