"""Local cart-only writer. Credentials stay in memory; uncertain POSTs never retry."""
import json
import re
import secrets
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from contextlib import contextmanager

import requests

BASE = 'https://fullmoon-distribution.fr/api'


def positive(value):
    if not re.fullmatch(r'[1-9][0-9]{0,9}', str(value)):
        raise ValueError('Identifiant PrestaShop invalide.')
    return int(value)


def parse_xml(raw):
    if len(raw) > 4_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Réponse XML refusée.')
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError('Réponse PrestaShop illisible.') from None


class CartWriter:
    def __init__(self):
        self._key = None

    def request(self, method, path, *, body=None, key=None, shop=None):
        allowed = (method == 'GET' and (path == '' or re.fullmatch(r'/carts/[1-9][0-9]*', path))) or (method == 'POST' and path == '/carts')
        if not allowed:
            raise ValueError('Cette connexion autorise seulement la lecture et la création de paniers.')
        credential = key or self._key
        if not credential:
            raise ValueError('Activez la clé dédiée aux paniers dans cette page.')
        # Fresh session: no proxy/netrc credentials, redirects, cookies or retries.
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.request(method, BASE + path, auth=(credential, ''),
                    params={'id_shop': positive(shop)} if shop is not None else None,
                    data=body, headers={'Content-Type': 'application/xml', 'Accept': 'application/xml'},
                    timeout=(10, 45), allow_redirects=False)
            if response.status_code not in (200, 201):
                raise ValueError('PrestaShop a refusé la requête panier. Vérifiez la clé et ses droits.')
            return parse_xml(response.content)
        except requests.RequestException:
            raise ValueError('Connexion à PrestaShop interrompue.') from None

    def verify_permissions(self, key=None, shop=None):
        root = self.request('GET', '', key=key, shop=shop)
        resources = root.find('api')
        if resources is None:
            raise ValueError('Permissions PrestaShop non vérifiables.')
        cart = resources.find('carts')
        if cart is None or any(cart.get(m) != 'true' for m in ('get', 'post')):
            raise ValueError('La clé dédiée doit autoriser GET et POST sur carts (paniers).')
        for resource in resources:
            if any(resource.get(m, 'false') != 'false' for m in ('put', 'delete', 'patch')) or (resource.tag != 'carts' and resource.get('post', 'false') != 'false'):
                raise ValueError('Clé trop permissive. Autorisez uniquement GET et POST sur carts, sans autre écriture.')

    def configure(self, key, shop):
        if not isinstance(key, str) or not re.fullmatch('[A-Za-z0-9]{32}', key):
            raise ValueError('Clé Webservice PrestaShop attendue (32 caractères). Ne saisissez pas de clé OpenAI.')
        self.verify_permissions(key, shop)
        self._key = key
        return {'ready': True}

    def clear(self):
        self._key = None
        return {'ready': False}


def merged_rows(result):
    if any(row.get('error') for row in result['rows']):
        raise ValueError('Corrigez toutes les lignes en erreur avant de créer le panier.')
    merged = {}
    for row in result['rows']:
        if row['accepted']:
            key = (positive(row['product_id']), int(row['attribute_id']))
            merged[key] = merged.get(key, 0) + positive(row['accepted'])
    if not merged:
        raise ValueError('Aucune quantité disponible pour ce panier.')
    return [{'id_product': p, 'id_product_attribute': a, 'quantity': q} for (p, a), q in sorted(merged.items())]


def expected_cart(profile, rows):
    fields = {k: int(profile[k]) for k in ('id_customer', 'id_address_delivery', 'id_address_invoice', 'id_currency', 'id_lang', 'id_shop', 'id_shop_group')}
    fields['rows'] = [{**row, 'id_address_delivery': fields['id_address_delivery']} for row in rows]
    return fields


def cart_xml(expected, secure_key):
    if not re.fullmatch(r'[a-fA-F0-9]{32}', secure_key or ''):
        raise ValueError('Identité sécurisée du client manquante dans PrestaShop.')
    root = ET.Element('prestashop')
    cart = ET.SubElement(root, 'cart')
    for name, value in expected.items():
        if name != 'rows':
            ET.SubElement(cart, name).text = str(value)
    for name, value in {'secure_key': secure_key, 'id_carrier': '0', 'recyclable': '0', 'gift': '0', 'allow_seperated_package': '0'}.items():
        ET.SubElement(cart, name).text = value
    items = ET.SubElement(ET.SubElement(cart, 'associations'), 'cart_rows')
    for row in expected['rows']:
        item = ET.SubElement(items, 'cart_row')
        for name, value in row.items():
            ET.SubElement(item, name).text = str(value)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def verify_cart(root, expected, cart_id):
    cart = root.find('cart')
    if cart is None or cart.findtext('id') != str(cart_id):
        raise ValueError('Panier créé mais son identifiant ne peut pas être vérifié.')
    for name, value in expected.items():
        if name != 'rows' and cart.findtext(name) != str(value):
            raise ValueError('Panier créé mais ses informations diffèrent. Vérifiez-le dans PrestaShop.')
    rows = []
    for item in cart.findall('./associations/cart_rows/cart_row'):
        rows.append({k: int(item.findtext(k, '-1')) for k in ('id_product', 'id_product_attribute', 'quantity', 'id_address_delivery')})
    key = lambda row: (row['id_product'], row['id_product_attribute'], row['id_address_delivery'])
    if sorted(rows, key=key) != sorted(expected['rows'], key=key):
        raise ValueError('Panier créé mais les quantités enregistrées diffèrent. Vérifiez-le dans PrestaShop.')


class CartService:
    def __init__(self, root):
        self.writer = CartWriter()
        self.folder = Path(root) / 'paniers'
        self.previews = {}

    @contextmanager
    def database(self):
        self.folder.mkdir(mode=0o700, exist_ok=True)
        self.folder.chmod(0o700)
        path = self.folder / 'registre.sqlite'
        db = sqlite3.connect(path)
        path.chmod(0o600)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS carts (operation TEXT PRIMARY KEY, order_ref TEXT NOT NULL, customer INTEGER NOT NULL, shop INTEGER NOT NULL, state TEXT NOT NULL, cart_id INTEGER, expected TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(order_ref,customer,shop))')
        try:
            with db:
                yield db
        finally:
            db.close()

    def history(self):
        with self.database() as db:
            return {'items': [dict(row) for row in db.execute('SELECT operation,order_ref,customer,shop,state,cart_id,created_at FROM carts ORDER BY created_at DESC LIMIT 30')]}

    def prepare(self, payload, result, profile):
        ref = str(payload.get('cart', {}).get('order_ref', '')).strip()
        if not 1 <= len(ref) <= 120:
            raise ValueError('Renseignez une référence de commande client (120 caractères maximum).')
        self.previews = {k: v for k, v in self.previews.items() if v['expires'] > time.time()}
        if len(self.previews) > 50:
            raise ValueError('Trop de préparations en cours. Relancez l’outil.')
        expected = expected_cart(profile, merged_rows(result))
        with self.database() as db:
            exists = db.execute('SELECT state,cart_id FROM carts WHERE order_ref=? AND customer=? AND shop=?', (ref, expected['id_customer'], expected['id_shop'])).fetchone()
        if exists:
            suffix = f" — panier n° {exists['cart_id']}" if exists['cart_id'] else ''
            raise ValueError('Cette référence a déjà fait l’objet d’un envoi' + suffix + '. Consultez le registre des paniers avant toute nouvelle action.')
        ticket = secrets.token_urlsafe(32)
        # Store the source server-side. The commit cannot supply arbitrary cart rows.
        clean = {k: payload[k] for k in ('rows', 'shop', 'vat', 'price_source', 'price_mode', 'cart')}
        self.previews[ticket] = {'payload': json.loads(json.dumps(clean)), 'expected': expected, 'order_ref': ref, 'expires': time.time() + 600}
        return {'ticket': ticket, 'result': result, 'customer': profile['label'], 'quantity': sum(row['quantity'] for row in expected['rows']), 'order_ref': ref}

    def preview(self, ticket):
        preview = self.previews.get(ticket)
        if preview is None or preview['expires'] < time.time():
            raise ValueError('Préparation expirée. Vérifiez à nouveau le panier.')
        return preview

    def existing(self, ticket):
        # Checked before a new stock read, including after process restart.
        with self.database() as db:
            record = db.execute('SELECT * FROM carts WHERE operation=?', (ticket,)).fetchone()
        return self.readback(dict(record)) if record else None

    def readback(self, record):
        if record['state'] == 'verified':
            return {'created': True, 'verified': True, 'cart_id': record['cart_id'], 'order_ref': record['order_ref'], 'reused': True}
        if not record['cart_id']:
            raise ValueError('Résultat de création incertain. Aucun nouvel envoi ne sera effectué. Vérifiez les paniers dans PrestaShop et le registre local.')
        try:
            expected = json.loads(record['expected'])
            root = self.writer.request('GET', f"/carts/{record['cart_id']}", shop=record['shop'])
            verify_cart(root, expected, record['cart_id'])
        except Exception:
            raise ValueError(f"Panier n° {record['cart_id']} créé, vérification incomplète. Contrôlez-le dans PrestaShop. Aucun nouvel envoi effectué.") from None
        with self.database() as db:
            db.execute("UPDATE carts SET state='verified' WHERE operation=?", (record['operation'],))
        return {'created': True, 'verified': True, 'cart_id': record['cart_id'], 'order_ref': record['order_ref']}

    def commit(self, ticket, result, profile):
        previous = self.existing(ticket)
        if previous:
            return previous
        preview = self.preview(ticket)
        expected = expected_cart(profile, merged_rows(result))
        if expected != preview['expected']:
            raise ValueError('Les quantités disponibles ou les informations client ont changé. Aucun panier créé. Préparez à nouveau le panier pour vérifier les nouvelles quantités.')
        body = cart_xml(expected, profile['secure_key'])
        # Persist intent before any POST. A crash or timeout must never cause a retry.
        with self.database() as db:
            try:
                db.execute('INSERT INTO carts(operation,order_ref,customer,shop,state,expected) VALUES(?,?,?,?,?,?)',
                    (ticket, preview['order_ref'], expected['id_customer'], expected['id_shop'], 'uncertain', json.dumps(expected)))
            except sqlite3.IntegrityError:
                raise ValueError('Envoi déjà enregistré. Consultez le registre des paniers.') from None
        try:
            root = self.writer.request('POST', '/carts', body=body, shop=expected['id_shop'])
            cart_id = positive(root.findtext('./cart/id', ''))
            with self.database() as db:
                db.execute("UPDATE carts SET state='created_unverified',cart_id=? WHERE operation=?", (cart_id, ticket))
        except Exception:
            raise ValueError('Résultat de création incertain. Vérifiez PrestaShop avant toute autre tentative. Cette référence est bloquée pour éviter un doublon.') from None
        return self.existing(ticket)
