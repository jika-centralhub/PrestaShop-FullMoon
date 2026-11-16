"""Clé panier : enregistrement local, reprise au lancement et refus par PrestaShop."""
import json, stat, sys, tempfile, unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import carts

KEY = 'A' * 32
PERMS = '<prestashop><api><carts get="true" post="true"/></api></prestashop>'
PROFILE = dict(id_customer=1, id_address_delivery=10, id_address_invoice=11, id_currency=1, id_lang=1,
               id_shop=1, id_shop_group=1, label='Client test', secure_key='a' * 32)
RESULT = {'rows': [{'product_id': 5, 'attribute_id': 0, 'accepted': 2}]}


class Response:
    def __init__(self, status, body=PERMS):
        self.status_code, self.content = status, body.encode()


class CartKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def service(self):
        return carts.CartService(self.root)

    def answer(self, status, body=PERMS):
        return patch('requests.Session.request', return_value=Response(status, body))

    def test_key_saved_privately_and_reloaded(self):
        with self.answer(200):
            self.service().writer.configure(KEY, 1)
        path = self.root / 'paniers' / 'cle-paniers.key'
        self.assertEqual(path.read_text(), KEY)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        writer = self.service().writer
        self.assertTrue(writer.ready)
        with self.answer(200):
            self.assertEqual(writer.status(1), {'ready': True, 'saved': True})

    def test_forget_erases_file(self):
        with self.answer(200):
            writer = self.service().writer
            writer.configure(KEY, 1)
        writer.clear()
        self.assertFalse((self.root / 'paniers' / 'cle-paniers.key').exists())
        self.assertFalse(self.service().writer.ready)

    def test_refused_saved_key_is_reported_and_forgotten(self):
        with self.answer(200):
            self.service().writer.configure(KEY, 1)
        writer = self.service().writer
        with self.answer(401):
            status = writer.status(1)
        self.assertFalse(status['ready'])
        self.assertIn('ne fonctionne plus', status['rejected'])
        self.assertFalse((self.root / 'paniers' / 'cle-paniers.key').exists())

    def test_permissions_removed_is_a_rejection(self):
        with self.answer(200):
            writer = self.service().writer
            writer.configure(KEY, 1)
        with self.answer(200, '<prestashop><api><carts get="true"/></api></prestashop>'):
            with self.assertRaises(carts.KeyRejected):
                writer.verify_permissions(shop=1)

    def test_rejected_post_releases_reference_and_creates_nothing(self):
        service = self.service()
        with self.answer(200):
            service.writer.configure(KEY, 1)
        ticket = service.prepare({'rows': [], 'shop': 1, 'price_source': 'catalog', 'cart': {'order_ref': 'CMD-1'}}, RESULT, PROFILE)['ticket']
        with self.answer(403):
            with self.assertRaises(carts.KeyRejected):
                service.commit(ticket, RESULT, PROFILE)
        with service.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM carts').fetchone()[0], 0)
        self.assertFalse(service.writer.ready)

    def test_other_errors_still_block_reference(self):
        service = self.service()
        with self.answer(200):
            service.writer.configure(KEY, 1)
        ticket = service.prepare({'rows': [], 'shop': 1, 'price_source': 'catalog', 'cart': {'order_ref': 'CMD-2'}}, RESULT, PROFILE)['ticket']
        with self.answer(500):
            with self.assertRaises(ValueError):
                service.commit(ticket, RESULT, PROFILE)
        with service.database() as db:
            self.assertEqual(db.execute("SELECT state FROM carts").fetchone()[0], 'uncertain')


if __name__ == '__main__':
    unittest.main()
