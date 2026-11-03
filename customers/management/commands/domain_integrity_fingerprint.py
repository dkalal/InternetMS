import hashlib
import json

from django.core.management.base import BaseCommand

from billing.models import BillingDocument, BillingLineItem, CustomerSubscription, SubscriptionPeriod
from customers.models import Customer, CustomerSite
from products.models import Product
from services.models import Package


class Command(BaseCommand):
    help = "Print stable hashes for domain and financial records without writing data."

    def handle(self, *args, **options):
        specs = {
            "customers": (Customer._base_manager, ["id", "tenant_id", "customer_type", "status", "name", "location", "address", "ip_address", "vlan_id"]),
            "sites": (CustomerSite._base_manager, ["id", "tenant_id", "customer_id", "name", "location", "address", "ip_address", "vlan_id", "is_primary"]),
            "packages": (Package._base_manager, ["id", "tenant_id", "name", "package_type", "monthly_fee", "setup_fee", "is_active"]),
            "subscriptions": (CustomerSubscription._base_manager, ["id", "tenant_id", "customer_id", "site_id", "package_id", "status", "start_date", "end_date", "monthly_fee_at_signup", "paid_through_date"]),
            "documents": (BillingDocument._base_manager, ["id", "tenant_id", "customer_id", "document_type", "number", "status", "subtotal", "tax_amount", "total"]),
            "lines": (BillingLineItem._base_manager, ["id", "tenant_id", "document_id", "product_id", "package_id", "quantity", "unit_price", "line_total"]),
            "periods": (SubscriptionPeriod._base_manager, ["id", "tenant_id", "subscription_id", "invoice_id", "receipt_id", "period_start", "period_end", "final_amount", "status"]),
            "products": (Product._base_manager, ["id", "tenant_id", "name", "quantity", "stock"]),
        }
        result = {}
        for name, (manager, fields) in specs.items():
            rows = list(manager.order_by("id").values(*fields))
            payload = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
            result[name] = {
                "count": len(rows),
                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            }
        self.stdout.write(json.dumps(result, sort_keys=True))
