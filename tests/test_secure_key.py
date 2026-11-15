"""Cart profile against the real MCP redaction: secure_key never transits the MCP."""
import asyncio, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'app'))
sys.path.append(str(PROJECT))
import app, carts, server

KEY = '0123456789abcdef0123456789abcdef'
PAYLOAD = dict(rows=[dict(reference='X', quantity=10)], shop='1', price_source='catalog',
               cart=dict(customer='1', delivery='10', billing='11', order_ref='TEST-SK'))


def customer_xml(customer_id='1', shop='1', secure_key=KEY):
    key = f'<secure_key><![CDATA[{secure_key}]]></secure_key>' if secure_key is not None else ''
    return ('<?xml version="1.0" encoding="UTF-8"?><prestashop><customer>'
            f'<id><![CDATA[{customer_id}]]></id><id_lang>1</id_lang><id_shop>{shop}</id_shop>'
            '<active>1</active><deleted>0</deleted><firstname>Test</firstname>'
            f'<email>client@example.test</email>{key}<passwd>x</passwd></customer></prestashop>')


class FakeClient:
    def __init__(self, xml):
        self.xml = xml
        self.reads = []

    def _fetch_xml(self, resource, id_):
        self.reads.append((resource, id_))
        return self.xml


class SecureKeyTests(unittest.TestCase):
    def mcp_customer(self):
        # Exactly what ps_get_record returns: server.py drops secure_key.
        return server._record_to_dict(ET.fromstring(customer_xml())[0])

    async def call(self, session, name, args):
        return {'customers': self.mcp_customer(), 'shops': dict(active='1', id_shop_group='1'),
                'countries': dict(active='1'), 'languages': dict(id='1', active='1')}[args['resource']]

    async def query(self, session, resource, filters, fields=None):
        if resource == 'addresses':
            return [dict(id=10, id_customer=1, deleted='0', id_country=8),
                    dict(id=11, id_customer=1, deleted='0', id_country=8)]
        return [dict(id='1', iso_code='EUR', active='1', deleted='0')]

    def profile(self, client):
        with patch.object(app, 'call', side_effect=self.call), patch.object(app, 'query', side_effect=self.query), \
                patch.object(app.PSClient, 'from_env', return_value=client):
            return asyncio.run(app.cart_profile(None, PAYLOAD, True))

    def test_mcp_still_redacts_secure_key(self):
        self.assertNotIn('secure_key', self.mcp_customer())
        self.assertNotIn('passwd', self.mcp_customer())

    def test_profile_reads_secure_key_locally_and_cart_is_built(self):
        client = FakeClient(customer_xml())
        profile = self.profile(client)
        self.assertEqual(profile['secure_key'], KEY)
        self.assertEqual(client.reads, [('customers', 1)])
        rows = carts.merged_rows({'rows': [dict(product_id=5, attribute_id=0, accepted=3, error='')]})
        body = carts.cart_xml(carts.expected_cart(profile, rows), profile['secure_key'])
        self.assertEqual(ET.fromstring(body).findtext('./cart/secure_key'), KEY)

    def test_full_commit_with_mcp_profile(self):
        profile = self.profile(FakeClient(customer_xml()))
        result = {'rows': [dict(product_id=5, attribute_id=0, accepted=3, error='')]}
        with tempfile.TemporaryDirectory() as folder:
            service = carts.CartService(Path(folder))
            prepared = service.prepare(PAYLOAD, result, profile)
            self.assertNotIn(KEY, json.dumps(prepared))
            sent = []

            def network(method, path, **kwargs):
                if method == 'POST':
                    sent.append(kwargs['body'])
                root = ET.fromstring(sent[0])
                ET.SubElement(root.find('cart'), 'id').text = '77'
                return root
            with patch.object(service.writer, 'request', side_effect=network):
                self.assertEqual(service.commit(prepared['ticket'], result, profile)['cart_id'], 77)
            self.assertEqual(len(sent), 1)
            self.assertNotIn(KEY.encode(), (service.folder / 'registre.sqlite').read_bytes())
            self.assertNotIn(KEY, json.dumps(service.previews))

    def test_rejects_missing_key_other_shop_or_other_customer(self):
        for xml, message in [(customer_xml(secure_key=None), 'Identité'), (customer_xml(secure_key='-1'), 'Identité'),
                             (customer_xml(shop='2'), 'boutique'), (customer_xml(customer_id='9'), 'boutique')]:
            with self.subTest(xml=xml), patch.object(app.PSClient, 'from_env', return_value=FakeClient(xml)):
                with self.assertRaisesRegex(ValueError, message):
                    app.customer_secure_key(1, 1)

    def test_connection_error_is_explained(self):
        with patch.object(app.PSClient, 'from_env', side_effect=RuntimeError('boom')):
            with self.assertRaisesRegex(ValueError, '.env'):
                app.customer_secure_key(1, 1)


if __name__ == '__main__':
    unittest.main()
