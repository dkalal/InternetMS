from __future__ import annotations

import contextlib
import io
import sys
from unittest.mock import patch

from django.test import SimpleTestCase

from .pdf import _load_weasyprint


class WeasyPrintLoaderTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        _load_weasyprint.cache_clear()

    def tearDown(self):
        _load_weasyprint.cache_clear()
        super().tearDown()

    @patch("billing.pdf.importlib.import_module")
    def test_failed_import_is_quietly_cached_and_logged_once(self, import_module):
        def fail_with_weasyprint_banner(_module_name):
            print("WeasyPrint native-library help banner", file=sys.stderr)
            raise OSError("cannot load library 'libpango'")

        import_module.side_effect = fail_with_weasyprint_banner
        stderr = io.StringIO()

        with self.assertLogs("billing.pdf", level="WARNING") as logs:
            with contextlib.redirect_stderr(stderr):
                first_result = _load_weasyprint()
                second_result = _load_weasyprint()

        self.assertIsNone(first_result)
        self.assertIsNone(second_result)
        import_module.assert_called_once_with("weasyprint")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("serving printable HTML instead of PDF", logs.output[0])
        self.assertIn("libpango", logs.output[0])

    @patch("billing.pdf.importlib.import_module")
    def test_successful_import_is_cached(self, import_module):
        backend = object()
        import_module.return_value = backend

        self.assertIs(_load_weasyprint(), backend)
        self.assertIs(_load_weasyprint(), backend)

        import_module.assert_called_once_with("weasyprint")
