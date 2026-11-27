"""Check the configured email transport without sending an email."""

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check SMTP connectivity, TLS negotiation, and authentication without sending mail."

    def handle(self, *args, **options):
        self.stdout.write(f"Email backend: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"SMTP host: {settings.EMAIL_HOST}")
        self.stdout.write(f"SMTP port: {settings.EMAIL_PORT}")
        self.stdout.write(f"TLS: {'enabled' if settings.EMAIL_USE_TLS else 'disabled'}")
        self.stdout.write(f"Implicit SSL: {'enabled' if settings.EMAIL_USE_SSL else 'disabled'}")
        self.stdout.write(f"Timeout: {settings.EMAIL_TIMEOUT}s")
        self.stdout.write(f"SMTP username configured: {'yes' if settings.EMAIL_HOST_USER else 'no'}")
        self.stdout.write(f"SMTP password configured: {'yes' if settings.EMAIL_HOST_PASSWORD else 'no'}")
        self.stdout.write(f"From address configured: {'yes' if settings.DEFAULT_FROM_EMAIL else 'no'}")

        if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
            self.stdout.write(self.style.WARNING("SMTP check skipped: the active backend is not Django SMTP."))
            return

        connection = get_connection(fail_silently=False)
        try:
            connection.open()
        except Exception as exc:
            # Do not print exception text: SMTP servers may include addresses or
            # provider-specific details. The exception class is enough to find
            # the failure family in Django/Python documentation and server logs.
            raise CommandError(
                "SMTP connection/TLS/authentication check failed "
                f"({type(exc).__name__}). Verify host, matching port and TLS/SSL mode, "
                "credentials, and outbound network access."
            ) from None
        else:
            try:
                connection.close()
            except Exception as exc:
                raise CommandError(
                    "SMTP connection opened, but the connection could not close cleanly "
                    f"({type(exc).__name__}). Check provider availability and network stability."
                ) from None
            self.stdout.write(self.style.SUCCESS("SMTP connection, TLS, and authentication succeeded."))
            self.stdout.write("No test email was sent; this does not verify sender acceptance or inbox delivery.")
