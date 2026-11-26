from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from billing.models import BillingDocument
from billing.services import BillingService, BillingServiceError, LineItemInput
from .models import InventoryBalance, StockContainer, ContainerAllocation, StockMovement
from .services import InventoryService, InventoryError
from .tests_packaging_receipts import ReceiptFixture
from .packaging_sales import sale_snapshot, suggest, replace_selections


@patch('products.packaging.workflows_ready', return_value=True)
class PackagingSaleTests(ReceiptFixture, TestCase):
    def setUp(self):
        super().setUp()
        with patch('products.packaging.workflows_ready', return_value=True):
            self.line()
            self.receive()

    def invoice(self, quantity, package=False, selections=None):
        snapshot = sale_snapshot(organization=self.org, product=self.product,
            packaging_id=self.package.pk if package else None, quantity=quantity, version=self.product.packaging_version)
        from types import SimpleNamespace
        candidate = SimpleNamespace(product_id=self.product.pk, packaging_id=self.package.pk if package else None, **snapshot)
        selected = selections if selections is not None else suggest(organization=self.org, line=candidate)
        return BillingService.create_document(organization=self.org, created_by=self.actor,
            document_type='invoice', customer_id=self.customer.pk, status='issued', issue_date=date.today(),
            sale_pricing_category='standard', tax_rate=Decimal('0'), items=[LineItemInput(
                product_id=self.product.pk, quantity=Decimal(quantity), product_packaging_id=self.package.pk if package else None,
                packaging_version=self.product.packaging_version, container_selections=tuple(selected))])

    def pay(self, invoice):
        return BillingService.create_receipt_from_invoice(organization=self.org, created_by=self.actor,
            invoice_id=invoice.pk, amount_paid=invoice.total, payment_method='cash', payment_reference=f'PACK-{invoice.pk}')

    def test_boxes_then_cuts_and_retry(self, ready):
        invoice = self.invoice('2', package=True)
        self.assertEqual(invoice.items.get().unit_snapshot, 'Box')
        self.assertEqual(invoice.items.get().base_quantity, Decimal('610'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))
        receipt = self.pay(invoice)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2440'))
        self.assertEqual(StockContainer.objects.filter(state='sealed').count(), 8)
        self.assertEqual(self.pay(invoice).pk, receipt.pk)
        self.pay(self.invoice('50'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2390'))
        self.assertEqual(StockContainer.objects.get(state='opened').remaining_quantity, Decimal('255'))
        self.pay(self.invoice('300'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2090'))
        self.assertEqual(StockContainer.objects.filter(state='sealed').count(), 6)
        self.assertEqual(StockContainer.objects.get(state='opened').remaining_quantity, Decimal('260'))
        self.assertEqual(ContainerAllocation.objects.count(), 5)

    def test_stale_piece_fails_payment_without_substitution(self, ready):
        first = self.invoice('1', package=True)
        stale = self.invoice('1', package=True)
        self.pay(first)
        before = BillingDocument.objects.filter(document_type='receipt').count()
        with self.assertRaises((InventoryError, BillingServiceError)):
            self.pay(stale)
        self.assertEqual(BillingDocument.objects.filter(document_type='receipt').count(), before)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2745'))

    def test_package_revenue_is_compared_per_base_unit(self, ready):
        from products.packaging import PackagingService
        PackagingService.update_prices(organization=self.org, actor=self.actor, product_id=self.product.pk,
            packaging_id=self.package.pk, prices={'standard_price': '306'})
        self.product.refresh_from_db()
        invoice = self.invoice('1', package=True)
        self.assertEqual(invoice.items.get().unit_price, Decimal('306'))
        self.pay(invoice)
        self.assertEqual(ContainerAllocation.objects.get().cost_total, Decimal('305'))

    def test_foreign_container_rejected_without_document(self, ready):
        before = BillingDocument.objects.count()
        with self.assertRaises(BillingServiceError):
            self.invoice('1', package=True, selections=[(999999999, Decimal('305'))])
        self.assertEqual(BillingDocument.objects.count(), before)

    def test_mixed_unit_cart_uses_base_threshold_and_independent_package_price(self, ready):
        from django.http import QueryDict
        from users.permissions import membership_for
        from products.models import Product
        from .models import Cart
        from .services import CartService
        Product.objects.unscoped().filter(pk=self.product.pk).update(allow_wholesale=True, wholesale_min_quantity=610, wholesale_price=Decimal('2'))
        self.product.refresh_from_db()
        cart = Cart.objects.create(tenant=self.org, organization=self.org, created_by=self.actor,
            customer=self.customer, sale_pricing_category='wholesale')
        containers = list(StockContainer.objects.order_by('pk'))
        def save(quantity, package, selected):
            data = QueryDict('', mutable=True)
            data.update(product=str(self.product.pk), quantity=quantity, discount_amount='0',
                sale_packaging=str(self.package.pk) if package else '', sale_configuration=str(self.product.packaging_version))
            for container, amount in selected:
                data[f'container_{container.pk}'] = str(amount)
            return CartService.save_line(organization=self.org, cart_id=cart.pk, data=data,
                actor=self.actor, membership=membership_for(self.actor, self.org))
        box = save('1', True, [(containers[0], 305)])
        self.assertEqual(box.unit_price, Decimal('900'))
        base = save('305', False, [(containers[1], 305)])
        box.refresh_from_db()
        self.assertEqual(box.unit_price, Decimal('700'))
        self.assertEqual(base.unit_price, Decimal('2'))
        self.assertEqual(cart.lines.count(), 2)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))
        invoice = CartService.convert(organization=self.org, cart_id=cart.pk, target='invoice', actor=self.actor,
            membership=membership_for(self.actor, self.org))
        self.assertEqual(list(invoice.items.order_by('id').values_list('base_quantity', flat=True)), [Decimal('305'), Decimal('305')])
        self.pay(invoice)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2440'))

    def test_separate_pieces_cannot_supply_whole_package(self, ready):
        self.pay(self.invoice('150'))
        opened = StockContainer.objects.get(state='opened')
        with self.assertRaises(BillingServiceError):
            self.invoice('1', package=True, selections=[(opened.pk, Decimal('305'))])

    def test_equal_net_revenue_after_discount_is_rejected(self, ready):
        from dataclasses import replace
        candidate = self.invoice('1', package=True)
        line = candidate.items.get()
        line.discount_amount = Decimal('595')
        line.line_total = Decimal('305')
        with self.assertRaises(BillingServiceError):
            BillingService._validate_product_cost_floors(organization=self.org, actor=self.actor, line_items=[line])

    def test_stale_configuration_fails_payment_and_preserves_historical_label(self, ready):
        from products.packaging import PackagingService
        invoice = self.invoice('1', package=True)
        PackagingService.update_prices(organization=self.org, actor=self.actor, product_id=self.product.pk,
            packaging_id=self.package.pk, prices={'standard_price': '950'})
        with self.assertRaises((InventoryError, BillingServiceError)):
            self.pay(invoice)
        self.assertEqual(invoice.items.get().unit_snapshot, 'Box')
        self.assertEqual(invoice.items.get().unit_price, Decimal('900'))
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))

    def test_api_commercial_units_and_reviewed_pieces_match_paid_stock(self, ready):
        from rest_framework.test import APIClient
        from rest_framework.authtoken.models import Token
        from integrations.models import IntegrationConsumer
        IntegrationConsumer.objects.create(user=self.actor, organization=self.org, name='Packaging API')
        token = Token.objects.create(user=self.actor)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        container = StockContainer.objects.order_by('pk').first()
        payload = {'customer_id': self.customer.pk, 'sale_pricing_category': 'standard', 'status': 'issued',
            'items': [{'product_id': self.product.pk, 'quantity': '1', 'product_packaging_id': self.package.pk,
                'packaging_version': self.product.packaging_version,
                'container_selections': [{'container_id': container.pk, 'base_quantity': '305'}]}]}
        response = client.post('/api/inventory/invoices/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['items'][0]['unit_snapshot'], 'Box')
        self.assertEqual(Decimal(response.data['items'][0]['base_quantity']), Decimal('305'))
        self.assertEqual(Decimal(response.data['items'][0]['unit_price']), Decimal('900'))
        invoice = BillingDocument.objects.get(pk=response.data['id'])
        paid = client.post(f'/api/inventory/invoices/{invoice.pk}/pay/', {'amount_paid': str(invoice.total),
            'payment_method': 'cash', 'payment_reference': 'PACKAGING-API'}, format='json')
        self.assertEqual(paid.status_code, 201, paid.data)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('2745'))

    def test_piece_review_page_enforces_invoice_access_and_saves_without_stock(self, ready):
        from django.urls import reverse
        invoice = self.invoice('1', package=True)
        self.client.force_login(self.actor)
        url = reverse('inventory:invoice_containers', args=[invoice.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Selections reserve no stock')
        container = StockContainer.objects.order_by('pk').last()
        line = invoice.items.get()
        response = self.client.post(url, {f'line_{line.pk}_container_{container.pk}': '305'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(line.container_selections.get().container_id, container.pk)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('3050'))

    def test_quotation_conversion_and_historical_receipt_keep_commercial_snapshot(self, ready):
        from products.packaging import PackagingService
        invoice = self.invoice('1', package=True)
        source = invoice.items.get()
        quotation = BillingService.create_document(organization=self.org, created_by=self.actor,
            document_type='quotation', customer_id=self.customer.pk, status='draft', sale_pricing_category='standard',
            items=[LineItemInput(product_id=self.product.pk, quantity=Decimal('1'), product_packaging_id=self.package.pk,
                packaging_version=self.product.packaging_version,
                container_selections=tuple(source.container_selections.values_list('container_id', 'base_quantity')))])
        converted = BillingService.create_invoice_from_quotation(organization=self.org, created_by=self.actor, quotation_id=quotation.pk)
        line = converted.items.get()
        self.assertEqual(line.unit_snapshot, 'Box')
        self.assertEqual(line.base_quantity, Decimal('305'))
        self.assertEqual(line.container_selections.count(), 1)
        from .packaging_sales import PackagingSelectionService
        from users.permissions import membership_for
        PackagingSelectionService.review_document(organization=self.org, actor=self.actor, document_id=converted.pk,
            membership=membership_for(self.actor, self.org), selections={line.pk: list(line.container_selections.values_list('container_id', 'base_quantity'))}, issue=True)
        receipt = self.pay(converted)
        PackagingService.update_prices(organization=self.org, actor=self.actor, product_id=self.product.pk,
            packaging_id=self.package.pk, prices={'standard_price': '950'})
        self.assertEqual(converted.items.get().unit_price, Decimal('900'))
        self.assertEqual(converted.items.get().unit_snapshot, 'Box')
        self.assertEqual(self.pay(converted).pk, receipt.pk)


from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections
from django.test import TransactionTestCase


@patch('products.packaging.workflows_ready', return_value=True)
class PackagingSaleConcurrencyTests(ReceiptFixture, TransactionTestCase):
    def test_box_and_piece_payment_compete_for_last_sealed_container(self, ready):
        self.line(quantity='1'); self.receive()
        container = StockContainer.objects.get()
        invoices = []
        for quantity, package in [('1', True), ('1', False)]:
            invoices.append(BillingService.create_document(organization=self.org, created_by=self.actor,
                document_type='invoice', customer_id=self.customer.pk, status='issued', tax_rate=Decimal('0'),
                sale_pricing_category='standard', items=[LineItemInput(product_id=self.product.pk,
                    quantity=Decimal(quantity), product_packaging_id=self.package.pk if package else None,
                    packaging_version=self.product.packaging_version,
                    container_selections=((container.pk, Decimal('305') if package else Decimal('1')),))]))
        def pay(invoice):
            close_old_connections()
            try:
                BillingService.create_receipt_from_invoice(organization=self.org, created_by=self.actor,
                    invoice_id=invoice.pk, amount_paid=invoice.total, payment_method='cash', payment_reference=f'RACE-{invoice.pk}')
                return True
            except (InventoryError, BillingServiceError):
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(pay, invoices))
        self.assertEqual(sum(result), 1)
        self.assertEqual(BillingDocument.objects.filter(document_type='receipt').count(), 1)
        self.assertEqual(ContainerAllocation.objects.count(), 1)
        self.assertIn(InventoryBalance.objects.get(product=self.product).quantity, [Decimal('0'), Decimal('304')])
        container.refresh_from_db()
        self.assertEqual(container.remaining_quantity, InventoryBalance.objects.get(product=self.product).quantity)
