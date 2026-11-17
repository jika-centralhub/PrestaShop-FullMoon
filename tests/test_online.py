"""Online mode: accounts, sessions, guards, shared registry and the HTTP server. Offline, no PrestaShop call."""
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import app, carts, online

HOST = 'outil.exemple.test'
ENV = {'FULLMOON_PUBLIC_URL': f'https://{HOST}', 'PS_SHOP_URL': 'https://fullmoon-distribution.fr/', 'PS_API_KEY': 'x' * 32,
       'PS_CART_KEY': 'C' * 32, 'FULLMOON_TRUST_PROXY': '1'}
PASSWORD = 'un mot de passe solide'


def settings(folder, **extra):
    return online.Settings({**ENV, 'FULLMOON_DATA_DIR': str(folder), **extra})


class Passwords(unittest.TestCase):
    def test_hash_roundtrip(self):
        stored = online.hash_password(PASSWORD)
        self.assertTrue(stored.startswith('scrypt$'))
        self.assertNotIn(PASSWORD, stored)
        self.assertTrue(online.check_password(PASSWORD, stored))
        self.assertFalse(online.check_password(PASSWORD + '!', stored))
        self.assertFalse(online.check_password(PASSWORD, 'abîmé'))

    def test_totp_rfc6238_vector(self):
        secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'  # "12345678901234567890"
        self.assertEqual(online.totp(secret, 59), '287082')
        self.assertTrue(online.check_totp(secret, '287 082', 59))
        self.assertTrue(online.check_totp(secret, online.totp(secret, 30), 59))   # one step of clock drift
        self.assertFalse(online.check_totp(secret, '000000', 59))
        self.assertFalse(online.check_totp(secret, '', 59))


class Accounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.accounts = online.Accounts(online.Database(sqlite_path=Path(self.tmp.name) / 'r.sqlite'))
        self.accounts.add('Marie', PASSWORD)

    def test_login_session_logout(self):
        token = self.accounts.login('marie', PASSWORD, '', '1.2.3.4')
        self.assertEqual(self.accounts.session(token), 'marie')
        self.accounts.logout(token)
        self.assertIsNone(self.accounts.session(token))
        self.assertIsNone(self.accounts.session('inventé'))

    def test_tokens_are_not_stored_in_clear(self):
        token = self.accounts.login('marie', PASSWORD, '', '1.2.3.4')
        db = sqlite3.connect(Path(self.tmp.name) / 'r.sqlite')
        self.addCleanup(db.close)
        stored = [row[0] for row in db.execute('SELECT token_hash FROM sessions')]
        self.assertNotIn(token, stored)

    def test_wrong_password_and_unknown_user_same_message(self):
        with self.assertRaises(PermissionError) as wrong:
            self.accounts.login('marie', 'mauvais', '', '1.2.3.4')
        with self.assertRaises(PermissionError) as unknown:
            self.accounts.login('personne', PASSWORD, '', '1.2.3.5')
        self.assertEqual(str(wrong.exception), str(unknown.exception))

    def test_throttle_after_five_failures(self):
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.accounts.login('marie', 'mauvais', '', '1.2.3.4')
        with self.assertRaisesRegex(PermissionError, 'Trop d’essais'):
            self.accounts.login('marie', PASSWORD, '', '9.9.9.9')   # same account, other address

    def test_session_expires_when_idle(self):
        token = self.accounts.login('marie', PASSWORD, '', '1.2.3.4')
        later = online.time.time() + online.IDLE + 5
        with patch.object(online.time, 'time', return_value=later):
            self.assertIsNone(self.accounts.session(token))

    def test_totp_required_when_enabled(self):
        secret = self.accounts.add('paul', PASSWORD, with_totp=True)
        with self.assertRaises(PermissionError):
            self.accounts.login('paul', PASSWORD, '', '1.2.3.4')
        self.assertEqual(self.accounts.session(self.accounts.login('paul', PASSWORD, online.totp(secret), '1.2.3.4')), 'paul')

    def test_disable_closes_sessions(self):
        token = self.accounts.login('marie', PASSWORD, '', '1.2.3.4')
        self.accounts.disable('marie')
        self.assertIsNone(self.accounts.session(token))
        with self.assertRaises(PermissionError):
            self.accounts.login('marie', PASSWORD, '', '1.2.3.4')

    def test_rules_for_new_accounts(self):
        with self.assertRaises(ValueError):
            self.accounts.add('jean', 'court')
        with self.assertRaises(ValueError):
            self.accounts.add('jean dupont', PASSWORD)


class SettingsAndGuards(unittest.TestCase):
    def test_public_url_must_be_https(self):
        with self.assertRaises(SystemExit):
            online.Settings({**ENV, 'FULLMOON_PUBLIC_URL': f'http://{HOST}'})

    def test_client_ip_uses_last_forwarded_entry(self):
        s = settings('/tmp/x')
        self.assertEqual(s.client_ip({'X-Forwarded-For': '6.6.6.6, 1.2.3.4'}, '10.0.0.1'), '1.2.3.4')
        self.assertEqual(s.client_ip({}, '10.0.0.1'), '10.0.0.1')

    def test_ip_allow_list(self):
        s = settings('/tmp/x', FULLMOON_ALLOWED_IPS='81.1.2.3, 192.168.0.0/24')
        self.assertTrue(s.ip_allowed('81.1.2.3'))
        self.assertTrue(s.ip_allowed('192.168.0.77'))
        self.assertFalse(s.ip_allowed('81.1.2.4'))
        self.assertFalse(s.ip_allowed('pas une adresse'))
        self.assertTrue(settings('/tmp/x').ip_allowed('8.8.8.8'))

    def test_server_cart_key_cannot_be_changed_from_the_page(self):
        writer = carts.CartWriter(fixed_key='C' * 32)
        with self.assertRaisesRegex(ValueError, 'administrateur'):
            writer.configure('D' * 32, 1)
        writer.clear()
        self.assertTrue(writer.ready)


PROFILE = dict(id_customer=1, id_address_delivery=10, id_address_invoice=11, id_currency=1, id_lang=1, id_shop=1, id_shop_group=1, label='Client test', secure_key='a' * 32)
RESULT = {'rows': [dict(product_id=5, attribute_id=0, accepted=3, error='')]}
PAYLOAD = dict(rows=[dict(reference='X', quantity=3)], shop='1', price_source='catalog', cart=dict(customer='1', delivery='10', billing='11', order_ref='TEST-1'))


class SharedRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = online.Database(sqlite_path=Path(self.tmp.name) / 'registre.sqlite')
        self.service = carts.CartService(Path(self.tmp.name), database=self.db, writer=carts.CartWriter(fixed_key='C' * 32))

    def network(self, method, path, **kwargs):
        root = ET.fromstring(carts.cart_xml(carts.expected_cart(PROFILE, carts.merged_rows(RESULT)), PROFILE['secure_key']))
        ET.SubElement(root.find('cart'), 'id').text = '99'
        return root

    def test_author_recorded_and_preparation_is_personal(self):
        ticket = self.service.prepare(PAYLOAD, RESULT, PROFILE, 'marie')['ticket']
        with patch.object(self.service.writer, 'request', side_effect=self.network):
            with self.assertRaisesRegex(ValueError, 'expirée'):
                self.service.commit(ticket, RESULT, PROFILE, 'paul')
            self.assertEqual(self.service.commit(ticket, RESULT, PROFILE, 'marie')['cart_id'], 99)
        item = self.service.history()['items'][0]
        self.assertEqual((item['created_by'], item['cart_id']), ('marie', 99))
        # Another person cannot send the same customer reference again.
        with self.assertRaisesRegex(ValueError, 'déjà fait l’objet'):
            self.service.prepare(PAYLOAD, RESULT, PROFILE, 'paul')

    def test_old_registry_gets_author_column(self):
        path = Path(self.tmp.name) / 'ancien.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE carts (operation TEXT PRIMARY KEY, order_ref TEXT NOT NULL, customer INTEGER NOT NULL, shop INTEGER NOT NULL, state TEXT NOT NULL, cart_id INTEGER, expected TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(order_ref,customer,shop))')
            db.execute("INSERT INTO carts(operation,order_ref,customer,shop,state,cart_id,expected) VALUES('t','ANCIEN',1,1,'verified',7,'{}')")
        db.close()
        service = carts.CartService(Path(self.tmp.name), database=online.Database(sqlite_path=path), writer=carts.CartWriter(fixed_key='C' * 32))
        item = service.history()['items'][0]
        self.assertEqual((item['order_ref'], item['created_by']), ('ANCIEN', None))


class Server(unittest.TestCase):
    """The real request handler in online mode, on a local port, without PrestaShop."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        saved = {name: getattr(app, name) for name in ('ONLINE', 'ACCOUNTS', 'CARTS', 'READS')}
        self.addCleanup(lambda: [setattr(app, k, v) for k, v in saved.items()])
        app.start_online(settings(self.tmp.name, FULLMOON_ALLOWED_IPS='1.2.3.4'))
        app.ACCOUNTS.add('marie', PASSWORD)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, cookie=None, host=HOST, origin=f'https://{HOST}', ip='1.2.3.4'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        headers = {'Host': host, 'X-Forwarded-For': ip}
        if origin:
            headers['Origin'] = origin
        if cookie:
            headers['Cookie'] = cookie
        if body is not None:
            body = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def login(self):
        response, _ = self.request('POST', '/connexion', {'username': 'marie', 'password': PASSWORD})
        self.assertEqual(response.status, 200)
        cookie = response.getheader('Set-Cookie')
        for flag in ('Secure', 'HttpOnly', 'SameSite=Strict', '__Host-'):
            self.assertIn(flag, cookie)
        return cookie.split(';')[0]

    def test_page_requires_login(self):
        response, _ = self.request('GET', '/', origin=None)
        self.assertEqual((response.status, response.getheader('Location')), (303, '/connexion'))
        response, _ = self.request('GET', '/ui.js', origin=None)
        self.assertEqual(response.status, 303)
        response, data = self.request('GET', '/connexion', origin=None)
        self.assertEqual(response.status, 200)
        self.assertIn('max-age', response.getheader('Strict-Transport-Security'))
        response, _ = self.request('POST', '/check', {'rows': []})
        self.assertEqual(response.status, 401)

    def test_login_then_page_and_history(self):
        cookie = self.login()
        response, data = self.request('GET', '/', cookie=cookie, origin=None)
        self.assertEqual(response.status, 200)
        self.assertIn(b'data-mode="online" data-user="marie"', data)
        response, data = self.request('POST', '/cart/history', {}, cookie=cookie)
        self.assertEqual((response.status, json.loads(data)), (200, {'items': []}))
        response, data = self.request('POST', '/cart/key', {'key': 'D' * 32, 'shop': 1}, cookie=cookie)
        self.assertEqual(response.status, 400)
        self.assertIn('administrateur', json.loads(data)['error'])
        self.request('POST', '/deconnexion', {}, cookie=cookie)
        response, _ = self.request('POST', '/cart/history', {}, cookie=cookie)
        self.assertEqual(response.status, 401)

    def test_wrong_password_refused(self):
        response, data = self.request('POST', '/connexion', {'username': 'marie', 'password': 'non'})
        self.assertEqual(response.status, 401)
        self.assertIsNone(response.getheader('Set-Cookie'))

    def test_guards(self):
        cookie = self.login()
        response, _ = self.request('GET', '/', cookie=cookie, host='autre.test', origin=None)
        self.assertEqual(response.status, 403)
        response, _ = self.request('POST', '/cart/history', {}, cookie=cookie, origin='https://attaquant.test')
        self.assertEqual(response.status, 403)
        response, _ = self.request('POST', '/cart/history', {}, cookie=cookie, origin=None)
        self.assertEqual(response.status, 403)
        response, _ = self.request('GET', '/connexion', origin=None, ip='5.6.7.8')
        self.assertEqual(response.status, 403)
        response, _ = self.request('POST', '/connexion', {'username': 'marie', 'password': PASSWORD}, ip='5.6.7.8')
        self.assertEqual(response.status, 403)

    def test_local_launch_url_is_not_served(self):
        response, _ = self.request('GET', '/', host='127.0.0.1:8790', origin=None)
        self.assertEqual(response.status, 403)


if __name__ == '__main__':
    unittest.main()
