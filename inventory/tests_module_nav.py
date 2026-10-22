from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve


class InventoryModuleNavigationTests(SimpleTestCase):
    template_name = "inventory/includes/module_nav.html"

    def render_nav(self, path, **permissions):
        request = RequestFactory().get(path)
        request.resolver_match = resolve(path)
        context = {
            "request": request,
            "INVENTORY_CAN_VIEW_PURCHASES": False,
            "INVENTORY_CAN_CREATE_PURCHASES": False,
            "INVENTORY_CAN_MANAGE_SUPPLIERS": False,
            "INVENTORY_CAN_VIEW_COSTS": False,
            **permissions,
        }
        return render_to_string(self.template_name, context)

    def test_each_inventory_area_has_one_clear_current_section(self):
        cases = (
            ("/inventory/", "Overview", {}),
            ("/products/", "Catalog", {}),
            ("/inventory/categories/", "Catalog", {}),
            ("/inventory/stock/adjust/", "Stock", {}),
            ("/inventory/movements/", "Movements", {}),
            ("/inventory/purchases/new/", "Purchases", {"INVENTORY_CAN_VIEW_PURCHASES": True}),
            ("/inventory/suppliers/", "Suppliers", {"INVENTORY_CAN_MANAGE_SUPPLIERS": True}),
            ("/inventory/carts/", "Selling", {}),
            ("/inventory/reports/sales/", "Reports", {}),
        )
        for path, label, permissions in cases:
            with self.subTest(path=path):
                html = self.render_nav(path, **permissions)
                self.assertEqual(html.count('aria-current="page"'), 1)
                self.assertIn(f'aria-current="page"', html)
                current_start = html.index('aria-current="page"')
                self.assertIn(f"<span>{label}</span>", html[current_start:current_start + 500])

    def test_permission_specific_links_do_not_leak(self):
        limited = self.render_nav("/inventory/")
        self.assertNotIn("Purchases</span>", limited)
        self.assertNotIn("Suppliers</span>", limited)
        self.assertIn('/inventory/reports/sales/', limited)

        manager = self.render_nav(
            "/inventory/",
            INVENTORY_CAN_VIEW_PURCHASES=True,
            INVENTORY_CAN_MANAGE_SUPPLIERS=True,
            INVENTORY_CAN_VIEW_COSTS=True,
        )
        self.assertIn("Purchases</span>", manager)
        self.assertIn("Suppliers</span>", manager)
        self.assertIn('/inventory/reports/', manager)

    def test_navigation_has_keyboard_scroll_region_and_decorative_icons(self):
        html = self.render_nav("/inventory/")
        self.assertIn('aria-label="Inventory sections"', html)
        self.assertIn('class="jims-module-nav__scroller" role="region" tabindex="0"', html)
        self.assertEqual(html.count('<svg aria-hidden="true"'), 6)
