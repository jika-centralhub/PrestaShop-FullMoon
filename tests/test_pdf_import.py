"""Text-PDF extraction on synthetic fixtures (fictitious data, see tests/fixtures/make_pdf_fixtures.py)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from pdf_import import read_pdf

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
HEADER = ['Référence fournisseur', 'Quantité', 'Nom du produit', 'Référence interne', 'EAN', 'Page']


def load(name):
    return read_pdf((FIXTURES / name).read_bytes())


class PdfImportTests(unittest.TestCase):
    def test_rows_keep_leading_zeros_and_rebuild_wrapped_cells(self):
        result = load('acheteur.pdf')
        self.assertTrue(result['pdf_review_required'])
        self.assertEqual(result['sheets'][0]['rows'], [
            HEADER,
            ['000456', '12', 'Produit fictif A', '00123', '0001234567890', '1'],
            ['ABC-0099', '1200', 'Produit fictif B sur deux lignes', '789', '3000000000017', '1'],
        ])

    def test_single_labelled_buyer_email(self):
        self.assertEqual(load('acheteur.pdf')['customer_hint']['email'], 'acheteur@example.test')

    def test_supplier_email_in_header_requires_manual_selection(self):
        result = load('acheteur-et-fournisseur.pdf')
        self.assertIsNone(result['customer_hint']['email'])
        self.assertNotIn('fournisseur@example.test', str(result['customer_hint']))

    def test_no_buyer_label_requires_manual_selection(self):
        self.assertIsNone(load('sans-acheteur.pdf')['customer_hint']['email'])

    def test_unknown_layout_refused_without_partial_import(self):
        with self.assertRaisesRegex(ValueError, 'Format PDF non reconnu'):
            load('format-inconnu.pdf')

    def test_non_pdf_refused(self):
        with self.assertRaisesRegex(ValueError, 'PDF invalide'):
            read_pdf(b'not a pdf')


if __name__ == '__main__':
    unittest.main()
