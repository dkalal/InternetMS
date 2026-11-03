"""Reproduce the seven-column clipboard layout without inferring price semantics."""
import json
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from openpyxl import Workbook

from .purchase_import import ImportInputError, parse_input


SAMPLE_TSV = '\n'.join([
    'TP-Link TL-SG1024\t7\t400\tTL-SG1024\t6\t\tTP-Link',
    'D-Link DGS-1210\t8\t450\tDGS-1210\t3\t\tD-Link',
    'Mikrotik CRS326\t9\t500\tCRS326\t2\t\tMikrotik',
    'Ubiquiti USW-24\t10\t550\tUSW-24\t3\t\tUbiquiti',
    'HP LaserJet M404dn\t11\t600\tLaserJet M404dn\t3\t\tHP',
    'Epson L3250\t12\t650\tL3250\t5\t\tEpson',
    'Canon i-SENSYS MF3010\t13\t700\tMF3010\t2\t\tCanon',
    'Brother HL-L2375DW\t14\t750\tHL-L2375DW\t4\t\tBrother',
    'Epson L4260\t15\t800\tL4260\t3\t\tEpson',
    'Hikvision DS-2CD2143G0\t16\t850\tDS-2CD2143G0\t8\t\tHikvision',
    'Dahua IPC-HDW1431T1\t17\t900\tIPC-HDW1431T1\t10\t\tDahua',
    'Uniview IPC3614SR3\t18\t950\tIPC3614SR3\t6\t\tUniview',
    'Hikvision DS-2CE16D0T\t19\t1000\tDS-2CE16D0T\t12\t\tHikvision',
])
# Test-only explicit choice, not a guess about the user's financial columns.
EXPLICIT_MAPPING = ['product_name', 'unit_cost', 'selling_price', 'model_number',
                    'quantity', 'sku', 'brand']


class PurchaseColumnMappingTests(SimpleTestCase):
    def test_reported_failure_is_reproduced_without_changing_numeric_validation(self):
        with self.assertRaisesMessage(ImportInputError, 'Row 1, unit cost'):
            parse_input(text=SAMPLE_TSV, input_format='mixed')

    def test_explicit_seven_column_mapping_preserves_every_record_and_blank_sku(self):
        rows = parse_input(text=SAMPLE_TSV, column_mapping=EXPLICIT_MAPPING, has_header=False)
        self.assertEqual(len(rows), 13)
        self.assertEqual(rows[0]['product_name'], 'TP-Link TL-SG1024')
        self.assertEqual(rows[0]['quantity'], '6')
        self.assertEqual(rows[0]['unit_cost'], '7')
        self.assertEqual(rows[0]['selling_price'], '400')
        self.assertEqual(rows[0]['model_number'], 'TL-SG1024')
        self.assertEqual(rows[0]['sku'], '')
        self.assertEqual(rows[0]['brand'], 'TP-Link')
        self.assertEqual(rows[-1]['row'], 13)

    def test_inspection_describes_structure_without_guessing_fields(self):
        from .purchase_import import inspect_input
        result = inspect_input(text=SAMPLE_TSV)
        self.assertEqual(result['row_count'], 13)
        self.assertEqual(result['column_count'], 7)
        self.assertFalse(result['has_header'])
        self.assertEqual(result['columns'][5]['examples'], ['(blank)'])
        self.assertTrue(all(column['field'] == '' for column in result['columns']))
        self.assertEqual(result['columns'][1]['examples'], ['7', '8', '9'])

    def test_mapping_requires_all_columns_and_distinct_required_targets(self):
        for mapping in (EXPLICIT_MAPPING[:-1], EXPLICIT_MAPPING + ['__ignore__'],
                        ['product_name', 'unit_cost', 'unit_cost', 'model_number', 'quantity', 'sku', 'brand'],
                        ['product_name', '', 'selling_price', 'model_number', 'quantity', 'sku', 'brand'],
                        ['product_name', 'tenant_id', 'selling_price', 'model_number', 'quantity', 'sku', 'brand'],
                        ['product_name', '__ignore__', 'selling_price', 'model_number', 'quantity', 'sku', 'brand'],
                        {'quantity': 1}, ['product_name', {}, 'unit_cost']):
            with self.subTest(mapping=mapping), self.assertRaises(ImportInputError):
                parse_input(text=SAMPLE_TSV, column_mapping=mapping, has_header=False)

    def test_explicit_ignore_and_header_choice_are_applied_without_losing_source_rows(self):
        text = 'Supplier item\tOrder count\tAcquisition cost\tInternal note\n\nRadio\t2\t10\tIgnore me'
        rows = parse_input(text=text, column_mapping=['product_name', 'quantity', 'unit_cost', '__ignore__'],
                           has_header=True)
        self.assertEqual(rows[0]['row'], 3)
        self.assertEqual(rows[0]['product_name'], 'Radio')
        self.assertNotIn('Internal note', json.dumps(rows))

    def test_upload_and_paste_share_the_mapping_and_text_identifier_contract(self):
        cells = ['New radio', '2', '1,250.50', '000123', '00001,00002']
        mapping = ['product_name', 'quantity', 'unit_cost', 'sku', 'serial_numbers']
        book = Workbook()
        book.active.title = 'Instructions'
        book.active.append(['Do not import'])
        sheet = book.create_sheet('Purchase rows')
        sheet.append(cells)
        output = BytesIO()
        book.save(output)
        expected = parse_input(text='\t'.join(cells), column_mapping=mapping, has_header=False)
        actual = parse_input(upload=SimpleUploadedFile('custom.xlsx', output.getvalue()),
                             column_mapping=mapping, has_header=False)
        self.assertEqual(expected, actual)
        self.assertEqual(actual[0]['sku'], '000123')
        self.assertEqual(actual[0]['serial_numbers'], '00001,00002')
        self.assertEqual(actual[0]['unit_cost'], '1250.50')

    def test_mapping_does_not_bypass_formula_or_numeric_validation(self):
        mapped = ['product_name', 'quantity', 'unit_cost']
        row = parse_input(text='Radio\t1\t=1+1', column_mapping=mapped, has_header=False)[0]
        self.assertIn('Formula', ' '.join(row['input_errors']))
        with self.assertRaisesMessage(ImportInputError, 'Source column 3'):
            parse_input(text='Radio\t1\tLatitude', column_mapping=mapped, has_header=False)

    def test_mapping_preserves_quotes_unicode_and_embedded_lines(self):
        rows = parse_input(text='\n"Kifaa cha redio\tα\nModel A"\t2\t10\n\nCable\t3\t2',
                           column_mapping=['product_name', 'quantity', 'unit_cost'], has_header=False)
        self.assertEqual([row['row'] for row in rows], [2, 5])
        self.assertEqual(rows[0]['product_name'], 'Kifaa cha redio\tα\nModel A')

    def test_limits_remain_enforced_during_column_inspection(self):
        from .purchase_import import inspect_input
        for text in ('X' * 100001, '\t'.join(str(i) for i in range(16)),
                     '\n'.join('Radio\t1\t10' for _ in range(201))):
            with self.subTest(size=len(text)), self.assertRaises(ImportInputError):
                inspect_input(text=text)
