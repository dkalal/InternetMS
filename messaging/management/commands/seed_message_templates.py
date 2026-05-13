from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from messaging.models import MessageTemplate


DEFAULT_TEMPLATES = [
    {
        "name": "INVOICE_NOTIFICATION",
        "category": MessageTemplate.Category.INVOICE,
        "content": (
            "Hello {{ customer_name }},\n\n"
            "Your invoice {{ invoice_number }} from {{ organization_name }} is ready. "
            "Amount: {{ amount }}. Due date: {{ due_date }}.\n\n"
            "Thank you."
        ),
        "variables_schema": {"optional": ["due_date"]},
    },
    {
        "name": "PAYMENT_REMINDER",
        "category": MessageTemplate.Category.REMINDER,
        "content": (
            "Hello {{ customer_name }},\n\n"
            "This is a friendly reminder from {{ organization_name }} about invoice {{ invoice_number }}. "
            "Outstanding amount: {{ amount }}. Due date: {{ due_date }}.\n\n"
            "Please arrange payment when possible."
        ),
        "variables_schema": {"optional": ["due_date"]},
    },
    {
        "name": "QUOTATION_READY",
        "category": MessageTemplate.Category.QUOTATION,
        "content": (
            "Hello {{ customer_name }},\n\n"
            "Your quotation {{ quotation_number }} from {{ organization_name }} is ready. "
            "Estimated amount: {{ amount }}.\n\n"
            "Please review it and let us know if you have any questions."
        ),
        "variables_schema": {"optional": []},
    },
    {
        "name": "GENERAL_FOLLOWUP",
        "category": MessageTemplate.Category.GENERAL,
        "content": (
            "Hello {{ customer_name }},\n\n"
            "This is a follow-up from {{ organization_name }}. Please let us know if you need any assistance."
        ),
        "variables_schema": {"optional": []},
    },
    {
        "name": "RECEIPT_NOTIFICATION",
        "category": MessageTemplate.Category.RECEIPT,
        "content": (
            "Hello {{ customer_name }},\n\n"
            "Payment received. Your receipt {{ receipt_number }} from {{ organization_name }} is ready. "
            "Amount: {{ amount }}.\n\n"
            "We will attach the receipt PDF here for your records."
        ),
        "variables_schema": {"optional": []},
    },
]


class Command(BaseCommand):
    help = "Seed global WhatsApp message templates."

    @transaction.atomic
    def handle(self, *args, **options):
        created = 0
        updated = 0
        for template_data in DEFAULT_TEMPLATES:
            template, was_created = MessageTemplate.objects.unscoped().update_or_create(
                tenant=None,
                name=template_data["name"],
                defaults={
                    "category": template_data["category"],
                    "content": template_data["content"],
                    "variables_schema": template_data["variables_schema"],
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded message templates. Created: {created}. Updated: {updated}.")) 
