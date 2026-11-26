import json
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import SimpleTestCase
from .packaging_transport import expand_fields


class PackagingTransportTests(SimpleTestCase):
    def test_bounded_large_payload_expands_without_global_field_limit_changes(self):
        values = {f'container_{index}': '1' for index in range(1, 2001)}
        source = QueryDict('', mutable=True)
        source['product'] = '7'
        source['container_payload'] = json.dumps(values)
        expanded = expand_fields(source, 'container_payload')
        self.assertEqual(len(expanded), 2001)
        self.assertEqual(expanded['container_2000'], '1')
        self.assertIn('container_payload', source)
        self.assertNotIn('container_payload', expanded)

    def test_duplicate_unknown_nested_and_oversized_fields_are_rejected(self):
        for raw in ['{"container_1":"1","container_1":"2"}', '{"factor":"305"}',
                    '{"container_1":{}}', json.dumps({f'container_{index}': '1' for index in range(1, 2002)})]:
            source = QueryDict('', mutable=True); source['container_payload'] = raw
            with self.assertRaises(ValidationError): expand_fields(source, 'container_payload')

    def test_direct_and_encoded_selection_cannot_be_ambiguous(self):
        source = QueryDict('', mutable=True)
        source['selection_payload'] = '{"line_1_container_2":"3"}'
        source['line_1_container_2'] = '4'
        with self.assertRaises(ValidationError): expand_fields(source, 'selection_payload')

    def test_repeated_payload_fields_are_rejected_in_either_order(self):
        for values in [('{}', '{"container_1":"3"}'), ('{"container_1":"3"}', '{}')]:
            source = QueryDict('', mutable=True)
            source.setlist('container_payload', values)
            with self.assertRaisesMessage(ValidationError, 'form data is invalid'):
                expand_fields(source, 'container_payload')
            self.assertEqual(source.getlist('container_payload'), list(values))