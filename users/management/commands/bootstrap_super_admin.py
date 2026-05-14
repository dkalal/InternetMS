from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.models import AuditLog
from users.models import Organization, UserAccessProfile


class Command(BaseCommand):
    help = "Create or promote a Django superuser and mark them as SUPER_ADMIN (tenantless)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--password", default=None)
        parser.add_argument(
            "--create",
            action="store_true",
            help="Create the user if missing (requires --password).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        username = options["username"]
        email = options["email"]
        password = options["password"]
        create = bool(options["create"])

        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if user is None:
            if not create:
                raise CommandError("User not found. Pass --create to create it.")
            if not password:
                raise CommandError("--password is required when using --create.")
            user = User.objects.create_superuser(username=username, email=email, password=password)
            self.stdout.write(self.style.SUCCESS(f"Created superuser: {username}"))
        else:
            user.is_staff = True
            user.is_superuser = True
            if email:
                user.email = email
            user.save(update_fields=["is_staff", "is_superuser", "email"])

        profile, created_profile = UserAccessProfile.objects.get_or_create(
            user=user,
            defaults={"role": UserAccessProfile.Role.SUPER_ADMIN, "tenant": None},
        )
        if not created_profile:
            profile.role = UserAccessProfile.Role.SUPER_ADMIN
            profile.tenant = None
            profile.save(update_fields=["role", "tenant", "updated_at"])

        # AuditLog requires an organization; use a stable default.
        org = Organization.objects.order_by("id").first()
        if org is None:
            org = Organization.objects.create(name="Default Tenant", slug="default-tenant", is_active=True)
        AuditLog.objects.create(
            organization=org,
            tenant=org,
            actor=user,
            action="security.super_admin.bootstrapped",
            object_type="User",
            object_id=str(user.id),
            metadata={"username": username},
        )

        self.stdout.write(self.style.SUCCESS(f"SUPER_ADMIN ready: {user.username} (id={user.id})"))

