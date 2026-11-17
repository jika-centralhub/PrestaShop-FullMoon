#!/usr/bin/env python3
"""Local order checker, read-only MCP and isolated cart-only writer."""
import asyncio, base64, csv, io, json, os, re, secrets, sys, threading, webbrowser, zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import asynccontextmanager

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT / 'vendor'))
sys.path.append(str(PROJECT))
import openpyxl
import xlrd
from carts import CartService, CartWriter, KeyRejected, positive, merged_rows, parse_xml
from pdf_import import read_pdf
from ps_client import PSClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import online
TOKEN = secrets.token_urlsafe(32)
PORT = 8790
LOCK = threading.Lock()
READS = LOCK            # local mode: one action at a time, as before
CENT = Decimal('0.01')
CARTS = CartService(ROOT)
ONLINE = None           # online.Settings once started with --online
ACCOUNTS = None
ASSETS = {'/assets/logo-fullmoon.png': 'image/png', '/assets/fullmoon-couleur.png': 'image/png',
          '/assets/favicon.png': 'image/png', '/assets/montserrat.woff2': 'font/woff2'}


def start_online(settings):
    """Shared registry, administrator's cart key, user accounts. Reads may run side by side."""
    global ONLINE, ACCOUNTS, CARTS, READS
    database = settings.database()
    writer = CartWriter(fixed_key=settings.cart_key) if settings.cart_key else CartWriter()
    writer.fixed = True
    ONLINE, ACCOUNTS = settings, online.Accounts(database)
    CARTS = CartService(ROOT, database=database, writer=writer)
    READS = threading.BoundedSemaphore(3)


@asynccontextmanager
async def mcp_session():
    params = StdioServerParameters(command=sys.executable, args=['-B',str(PROJECT/'server.py')],cwd=str(PROJECT))
    with open(os.devnull, 'w') as errors:
        async with stdio_client(params, errlog=errors) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                yield session


async def customer_lookup(payload):
    term = str(payload.get('search', '')).strip()
    shop = positive(payload.get('shop'))
    if not term or len(term) > 254 or any(c in term for c in '[]|%<>'):
        raise ValueError('Saisissez l’identifiant client ou son adresse e-mail exacte.')
    fields = ['id', 'firstname', 'lastname', 'company', 'email', 'active', 'deleted', 'id_shop']
    async with mcp_session() as session:
        found = await query(session, 'customers', {'id' if term.isdigit() else 'email': term}, fields)
        customers = [c for c in found if str(c.get('active')) == '1' and str(c.get('deleted', '0')) == '0' and str(c.get('id_shop')) == str(shop)]
        if not customers:
            raise ValueError('Aucun client actif correspondant dans cette boutique.')
        return {'customers': [{k: c.get(k, '') for k in ('id', 'firstname', 'lastname', 'company', 'email')} for c in customers]}


async def customer_directory(payload):
    shop = positive(payload.get('shop'))
    customers = {}
    # The e-mail is included so the page can search by partial address locally. It stays in memory, in the page.
    fields = ['id', 'firstname', 'lastname', 'company', 'email']
    async with mcp_session() as session:
        for offset in range(0, 20000, 200):
            page = await call(session, 'ps_query', {'resource': 'customers',
                'filters': {'id_shop': str(shop), 'active': '1', 'deleted': '0'},
                'fields': fields, 'sort': 'id_ASC', 'limit': 200, 'offset': offset})
            for customer in page:
                identifier = positive(customer['id'])
                customers[identifier] = {key: str(customer.get(key) or '') for key in fields}
            if len(page) < 200:
                break
        else:
            raise ValueError('Répertoire trop volumineux. Aucun résultat partiel affiché.')
    return {'customers': sorted(customers.values(), key=lambda c: (c['company'].casefold(), c['lastname'].casefold(), c['firstname'].casefold(), int(c['id']))),
        'checked_at': datetime.now().astimezone().isoformat(timespec='seconds')}


async def customer_match(payload):
    email = str(payload.get('email', '')).strip().casefold()
    shop = positive(payload.get('shop'))
    if not email or '@' not in email:
        return {'customers': [], 'status': 'missing'}
    if len(email) > 254 or any(c in email for c in '[]|%<>\r\n'):
        raise ValueError('Adresse e-mail invalide pour la recherche.')
    fields = ['id', 'firstname', 'lastname', 'company', 'email', 'active', 'deleted', 'id_shop']
    async with mcp_session() as session:
        found = await query(session, 'customers', {'email': email, 'id_shop': str(shop), 'active': '1', 'deleted': '0'}, fields)
    customers = [{k: c.get(k, '') for k in ('id', 'firstname', 'lastname', 'company', 'email')} for c in found
        if str(c.get('email', '')).strip().casefold() == email
        and str(c.get('active')) == '1' and str(c.get('deleted', '0')) == '0'
        and str(c.get('id_shop')) == str(shop)]
    return {'customers': customers, 'status': 'unique' if len(customers) == 1 else 'ambiguous' if customers else 'missing'}


async def customer_contact(payload):
    shop = positive(payload.get('shop'))
    customer_id = positive(payload.get('customer'))
    async with mcp_session() as session:
        customers = await query(session, 'customers',
            {'id': str(customer_id), 'id_shop': str(shop), 'active': '1', 'deleted': '0'},
            ['id', 'firstname', 'lastname', 'company', 'email'])
        if len(customers) != 1 or str(customers[0]['id']) != str(customer_id):
            raise ValueError('Client actif introuvable dans cette boutique.')
        fields = ['id', 'alias', 'company', 'firstname', 'lastname', 'address1', 'address2', 'postcode', 'city', 'id_country']
        addresses = await query(session, 'addresses', {'id_customer': str(customer_id), 'deleted': '0'}, fields)
        countries = {}
        for address in addresses:
            country_id = positive(address['id_country'])
            if country_id not in countries:
                country = await query(session, 'countries', {'id': str(country_id)}, ['id', 'name'])
                countries[country_id] = str(country[0].get('name', '')) if len(country) == 1 else ''
        return {'customer': {k: str(customers[0].get(k) or '') for k in ('id', 'firstname', 'lastname', 'company', 'email')},
            'addresses': [{**{k: str(a.get(k) or '') for k in fields if k != 'id_country'},
                'country': countries[positive(a['id_country'])]} for a in addresses]}


async def cart_profile(session, payload, selected=False):
    choice = payload.get('cart', {})
    shop_id = positive(payload.get('shop'))
    customer_id = positive(choice.get('customer'))
    async def record(resource, record_id):
        return await call(session, 'ps_get_record', {'resource': resource, 'record_id': positive(record_id)})
    customer = await record('customers', customer_id)
    if str(customer.get('active')) != '1' or str(customer.get('deleted', '0')) != '0' or str(customer.get('id_shop')) != str(shop_id):
        raise ValueError('Client inactif ou associé à une autre boutique.')
    shop = await record('shops', shop_id)
    if str(shop.get('active')) != '1':
        raise ValueError('Boutique inactive.')
    addresses = await query(session, 'addresses', {'id_customer': str(customer_id)}, ['id', 'id_customer', 'alias', 'company', 'firstname', 'lastname', 'address1', 'address2', 'postcode', 'city', 'id_country', 'deleted'])
    addresses = [a for a in addresses if str(a.get('deleted', '0')) == '0' and str(a.get('id_customer')) == str(customer_id)]
    label = ' · '.join(str(customer.get(k, '')) for k in ('company', 'firstname', 'lastname', 'email') if customer.get(k))
    if not selected:
        return {'customer': label, 'addresses': [{k: a.get(k, '') for k in ('id', 'alias', 'company', 'address1', 'address2', 'postcode', 'city')} for a in addresses]}
    delivery = positive(choice.get('delivery'))
    billing = positive(choice.get('billing'))
    by_id = {positive(a['id']): a for a in addresses}
    if delivery not in by_id or billing not in by_id:
        raise ValueError('Sélectionnez des adresses existantes appartenant à ce client.')
    for country_id in {by_id[delivery]['id_country'], by_id[billing]['id_country']}:
        country = await record('countries', country_id)
        if str(country.get('active')) != '1':
            raise ValueError('Le pays d’une adresse est désactivé dans PrestaShop.')
    currencies = await query(session, 'currencies', {'iso_code': 'EUR'}, ['id', 'iso_code', 'active', 'deleted'])
    currencies = [c for c in currencies if c.get('iso_code') == 'EUR' and str(c.get('active')) == '1' and str(c.get('deleted', '0')) == '0']
    if len(currencies) != 1:
        raise ValueError('Devise EUR active non identifiable. Vérification manuelle requise.')
    language = await record('languages', customer.get('id_lang'))
    if str(language.get('active')) != '1':
        raise ValueError('Langue du client inactive.')
    # The MCP redacts secure_key; read it locally, outside the MCP (see customer_secure_key).
    secure_key = await asyncio.to_thread(customer_secure_key, customer_id, shop_id)
    return {'id_customer': customer_id, 'id_address_delivery': delivery, 'id_address_invoice': billing,
        'id_currency': positive(currencies[0]['id']), 'id_lang': positive(language['id']), 'id_shop': shop_id,
        'id_shop_group': positive(shop['id_shop_group']), 'label': label, 'secure_key': secure_key}


def customer_secure_key(customer_id, shop_id):
    """Read the customer's secure_key with the local read-only key from .env.

    server.py redacts this field so that it never reaches ChatGPT through the MCP.
    PrestaShop links a cart to its customer with it, so it is read here, directly
    and outside the MCP. It stays in this process's memory and is only sent in the
    cart POST: never returned to the page, logged or written to the ledger."""
    try:
        raw = PSClient.from_env(str(PROJECT / '.env'))._fetch_xml('customers', positive(customer_id))
    except Exception:
        raise ValueError('Lecture du client impossible pour préparer le panier. Vérifiez la clé en lecture seule du fichier .env et la connexion.') from None
    customer = parse_xml(raw.encode('utf-8')).find('customer')
    if customer is None or (customer.findtext('id') or '').strip() != str(customer_id) or (customer.findtext('id_shop') or '').strip() != str(shop_id):
        raise ValueError('Client modifié ou associé à une autre boutique. Vérification manuelle requise.')
    secure_key = (customer.findtext('secure_key') or '').strip()
    if not re.fullmatch(r'[a-fA-F0-9]{32}', secure_key):
        raise ValueError('Identité sécurisée du client manquante dans PrestaShop.')
    return secure_key


async def cart_details(payload):
    async with mcp_session() as session:
        return await cart_profile(session, payload)


async def checked_cart(payload):
    # Business constraints first; stock reads come last, as close as possible to POST.
    async with mcp_session() as session:
        profile = await cart_profile(session, payload, selected=True)
        rows = payload.get('rows', [])
        if not rows or len(rows) > 500:
            raise ValueError('Importez entre 1 et 500 lignes de commande.')
        catalog, minima = {}, {}
        for ref in dict.fromkeys(str(r.get('reference', '')).strip() for r in rows):
            try:
                if not ref:
                    raise ValueError('Référence manquante.')
                item = await resolve(session, ref, profile['id_shop'])
            except ValueError as error:
                raise ValueError(f'Référence {ref or "vide"} : {error} Retirez cette ligne de la commande ou corrigez le produit dans PrestaShop.') from None
            product = await call(session, 'ps_get_record', {'resource': 'products', 'record_id': item['product_id']})
            if str(product.get('available_for_order')) != '1':
                raise ValueError(f'Produit {ref} indisponible à la commande.')
            if str(product.get('customizable', '0')) != '0' or product.get('type') == 'pack' or str(product.get('cache_is_pack', '0')) == '1':
                raise ValueError(f'Produit {ref} personnalisable ou en pack. Préparez-le manuellement dans PrestaShop.')
            minimum = product.get('minimal_quantity', '1')
            if item['attribute_id']:
                variant = await call(session, 'ps_get_record', {'resource': 'combinations', 'record_id': item['attribute_id']})
                minimum = variant.get('minimal_quantity', '1')
            minima[(item['product_id'], item['attribute_id'])] = max(1, int(minimum or 1))
            catalog[ref] = item
        # Refresh each stock record, including references pointing at the same stock.
        fresh = {}
        for item in catalog.values():
            if item['stock_id'] not in fresh:
                stock = await call(session, 'ps_get_record', {'resource': 'stock_availables', 'record_id': positive(item['stock_id'])})
                if (str(stock.get('id_shop')), str(stock.get('id_product')), str(stock.get('id_product_attribute'))) != (str(profile['id_shop']), str(item['product_id']), str(item['attribute_id'])):
                    raise ValueError('Stock modifié ou ambigu. Vérification manuelle requise.')
                fresh[item['stock_id']] = max(0, int(stock['quantity']))
            item['available'] = fresh[item['stock_id']]
        result = allocate(rows, catalog, 'catalog')
        for row in merged_rows(result):
            if row['quantity'] < minima[(row['id_product'], row['id_product_attribute'])]:
                raise ValueError('Une quantité disponible est inférieure au minimum de vente du produit. Corrigez la commande avant de créer le panier.')
        return result, profile


def cart_route(path, payload, user=None):
    def run(awaitable):
        try:
            return asyncio.run(asyncio.wait_for(awaitable, timeout=240))
        except ExceptionGroup as group:
            def validation_error(exc):
                if isinstance(exc, ValueError):
                    return exc
                if isinstance(exc, BaseExceptionGroup):
                    for child in exc.exceptions:
                        found = validation_error(child)
                        if found is not None:
                            return found
            error = validation_error(group)
            if error is not None:
                raise ValueError(str(error)) from None
            raise
    if path == '/cart/key':
        return CARTS.writer.configure(payload.get('key'), positive(payload.get('shop')))
    if path == '/cart/status':
        return CARTS.writer.status(positive(payload.get('shop')))
    if path == '/cart/forget':
        return CARTS.writer.clear()
    if path == '/cart/history':
        return CARTS.history()
    if path == '/cart/customers':
        return run(customer_lookup(payload))
    if path == '/cart/details':
        return run(cart_details(payload))
    if path == '/cart/prepare':
        CARTS.writer.verify_permissions(shop=positive(payload.get('shop')))
        result, profile = run(checked_cart(payload))
        return CARTS.prepare(payload, result, profile, user)
    if path == '/cart/create':
        ticket = str(payload.get('ticket', ''))
        previous = CARTS.existing(ticket)
        if previous:
            return previous
        prepared = CARTS.preview(ticket, user)
        CARTS.writer.verify_permissions(shop=positive(prepared['payload']['shop']))
        result, profile = run(checked_cart(prepared['payload']))
        created = CARTS.commit(ticket, result, profile, user)
        if ONLINE:
            print(f"Panier n° {created.get('cart_id')} créé par {user}.", flush=True)
        return {**created, 'result': result}
    raise ValueError('Action panier inconnue.')


def number(value, label, integer=False):
    try:
        result = Decimal(str(value).strip().replace('\u202f','').replace('\xa0','').replace(' ', '').replace(',', '.'))
    except InvalidOperation:
        raise ValueError(f'{label} invalide.') from None
    if not result.is_finite() or result < 0 or result > 100000000:
        raise ValueError(f'{label} hors limites.')
    if integer and result != result.to_integral_value():
        raise ValueError(f'{label} doit être un entier.')
    return result


def read_csv(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
    if not text.strip():
        raise ValueError("Le fichier CSV est vide.")
    first, _, rest = text.partition("\n")
    if first.strip().lower().startswith("sep=") and len(first.strip()) == 5:
        separator = first.strip()[-1]
        if separator not in ";,\t|":
            raise ValueError("Séparateur CSV non pris en charge.")
        text = rest
    else:
        try:
            separator = csv.Sniffer().sniff(text[:65536], delimiters=";,\t|").delimiter
        except csv.Error:
            raise ValueError("Séparateur CSV indétectable. Utilisez point-virgule, virgule ou tabulation.") from None
    rows = []
    try:
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=separator, strict=True):
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) > 50 or len(rows) >= 1001:
                raise ValueError("CSV limité à 1001 lignes et 50 colonnes.")
            rows.append(row)
    except csv.Error:
        raise ValueError("CSV mal formé. Vérifiez les guillemets et les séparateurs.") from None
    if not rows or len(rows[0]) < 2:
        raise ValueError("Le CSV doit contenir au moins une colonne référence et une colonne quantité.")
    return rows


def read_order_file(payload):
    data = base64.b64decode(payload['data'], validate=True)
    if len(data) > 8_000_000:
        raise ValueError('Fichier limité à 8 Mo.')
    suffix = Path(payload['name']).suffix.lower()
    sheets = []
    if suffix == '.pdf':
        return read_pdf(data)
    if suffix == '.csv':
        sheets.append({'name':Path(payload['name']).stem,'rows':read_csv(data)})
    elif suffix == '.xlsx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(x.file_size for x in archive.infolist()) > 50_000_000:
                raise ValueError('Classeur décompressé trop volumineux.')
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
        try:
            for sheet in book:
                if sheet.max_row > 1001 or sheet.max_column > 50:
                    raise ValueError("Feuille limitée à 1001 lignes et 50 colonnes. Exportez uniquement la commande.")
                rows = []
                for row in sheet.iter_rows(max_row=sheet.max_row, max_col=sheet.max_column):
                    values = []
                    for cell in row:
                        value = cell.value
                        # Preserve numeric SKU cells formatted with leading zeroes.
                        if isinstance(value, (int, float)) and value == int(value) and cell.number_format and set(cell.number_format) == {'0'}:
                            value = str(int(value)).zfill(len(cell.number_format))
                        elif isinstance(value, (int, float)) and value == int(value):
                            value = str(int(value))
                        values.append('' if value is None else str(value))
                    rows.append(values)
                sheets.append({'name':sheet.title, 'rows':rows})
        finally:
            book.close()
    elif suffix == '.xls':
        book = xlrd.open_workbook(file_contents=data, formatting_info=True)
        for sheet in book.sheets():
            if sheet.nrows > 1001 or sheet.ncols > 50:
                raise ValueError("Feuille limitée à 1001 lignes et 50 colonnes.")
            rows = []
            for i in range(min(sheet.nrows, 1002)):
                row = []
                for j in range(min(sheet.ncols, 50)):
                    cell = sheet.cell(i,j)
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_NUMBER and value == int(value):
                        fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
                        value = str(int(value)).zfill(len(fmt)) if fmt and set(fmt)=={'0'} else str(int(value))
                    row.append(str(value))
                rows.append(row)
            sheets.append({'name':sheet.name,'rows':rows})
    else:
        raise ValueError('Choisissez un fichier .xlsx, .xls, .csv ou .pdf.')
    return {'sheets':sheets}


async def call(session, name, args):
    result = await session.call_tool(name, args)
    if result.isError:
        raise ValueError('Lecture MCP impossible. Vérifiez la connexion et les permissions GET.')
    data = result.structuredContent
    if data is None:
        data = json.loads(next(x.text for x in result.content if x.type == 'text'))
    return data.get('result', data) if isinstance(data,dict) else data


async def query(session, resource, filters, fields=None):
    data = await call(session, 'ps_query', {'resource':resource,'filters':filters,'fields':fields,'limit':200})
    if len(data) >= 200:
        raise ValueError('Trop de correspondances. Précisez la référence ou la boutique.')
    return data


async def resolve(session, reference, shop):
    if any(c in reference for c in '[]|%<>'):
        raise ValueError('Référence contenant un caractère de filtre non pris en charge.')
    products = await query(session,'products',{'reference':reference},['id','reference','name','price','active'])
    products = [p for p in products if p.get('reference') == reference]
    combinations = await query(session,'combinations',{'reference':reference},['id','id_product','reference','price'])
    combinations = [c for c in combinations if c.get('reference') == reference]
    if len(products) + len(combinations) != 1:
        raise ValueError('Référence inconnue.' if not products and not combinations else 'Référence ambiguë dans le catalogue.')
    if combinations:
        variant = combinations[0]
        product = await call(session,'ps_get_record',{'resource':'products','record_id':int(variant['id_product'])})
        attribute = int(variant['id'])
        price = Decimal(product['price']) + Decimal(variant['price'])
    else:
        product = products[0]
        attribute = 0
        variants = await query(session,'combinations',{'id_product':str(product['id'])},['id'])
        if variants:
            raise ValueError('Produit à déclinaisons. Utilisez la référence exacte de la déclinaison.')
        price = Decimal(product['price'])
    if str(product.get('active')) != '1':
        raise ValueError('Produit inactif dans la boutique.')
    stocks = await query(session,'stock_availables',{'id_product':str(product['id']),'id_product_attribute':str(attribute)})
    stocks = [s for s in stocks if str(s.get('id_shop')) == str(shop)]
    if len(stocks) != 1:
        raise ValueError('Stock absent, partagé entre boutiques ou ambigu. Vérification manuelle requise.')
    stock = stocks[0]
    return {'name':product['name'],'stock_id':str(stock['id']),'available':max(0,int(stock['quantity'])),'catalog_price':str(price),'product_id':int(product['id']),'attribute_id':attribute}


def allocate(rows, catalog, source):
    used = {}
    output = []
    total_ht = Decimal(0)
    for index, raw in enumerate(rows):
        ref = str(raw.get('reference','')).strip()
        line = {'reference':ref,'line':raw.get('line',index+1),'requested':raw.get('quantity',''),'accepted':0,'error':''}
        try:
            quantity = int(number(raw.get('quantity'), 'Quantité', True))
            if quantity == 0:
                raise ValueError('Quantité commandée nulle.')
            item = catalog[ref]
            if 'error' in item:
                raise ValueError(item['error'])
            unit = number(raw.get('price'), 'Prix') if source == 'excel' else number(item['catalog_price'],'Prix catalogue')
            unit = unit.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
            remaining = max(0,item['available'] - used.get(item['stock_id'],0))
            accepted = min(quantity, remaining)
            used[item['stock_id']] = used.get(item['stock_id'],0) + accepted
            subtotal = (unit * accepted).quantize(CENT, rounding=ROUND_HALF_UP)
            total_ht += subtotal
            line.update(item, requested=quantity, accepted=accepted, missing=quantity-accepted, unit_ht=str(unit), subtotal=str(subtotal))
        except (ValueError, KeyError, InvalidOperation) as error:
            line['error'] = str(error)
        output.append(line)
    return {'rows':output,'ht':str(total_ht),'checked_at':datetime.now().astimezone().isoformat(timespec='seconds')}


async def check(payload):
    rows = payload.get('rows',[])
    if not rows or len(rows)>500:
        raise ValueError('Importez entre 1 et 500 lignes de commande.')
    if payload.get('price_source') not in ('excel','catalog') or payload.get('price_mode', 'ht') != 'ht':
        raise ValueError('Choisissez des prix hors taxes. Les prix TTC ne sont plus pris en charge.')
    shop = int(number(payload.get('shop'), 'Identifiant boutique', True))
    if shop < 1:
        raise ValueError('Identifiant boutique requis.')
    catalog = {}
    params = StdioServerParameters(command=sys.executable, args=['-B',str(PROJECT/'server.py')],cwd=str(PROJECT))
    with open(os.devnull,'w') as errors:
        async with stdio_client(params,errlog=errors) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                await session.initialize()
                for ref in dict.fromkeys(str(r.get('reference','')).strip() for r in rows):
                    try:
                        if not ref:
                            raise ValueError('Référence manquante.')
                        catalog[ref] = await resolve(session,ref,shop)
                    except ValueError as error:
                        catalog[ref] = {'error':str(error)}
    return allocate(rows,catalog,payload['price_source'])


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def respond(self,status,data,kind='application/json',headers=()):
        if isinstance(data, bytes):
            raw, content_type = data, kind
        else:
            raw = json.dumps(data,ensure_ascii=False).encode() if kind=='application/json' else data.encode()
            content_type = kind+'; charset=utf-8'
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if ONLINE and ONLINE.https:
            self.send_header('Strict-Transport-Security','max-age=31536000')
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    # ── Online mode: who is calling? ──
    def client_ip(self):
        return ONLINE.client_ip(self.headers, self.client_address[0])

    def session_token(self):
        return online.cookie_value(self.headers.get('Cookie'))

    def current_user(self):
        return ACCOUNTS.session(self.session_token())

    def online_refused(self, post):
        """Return an error already sent, or None when the request may go on."""
        if self.headers.get('Host') != ONLINE.host:
            self.respond(403,{'error':'Hôte refusé.'}); return True
        if not ONLINE.ip_allowed(self.client_ip()):
            self.respond(403,{'error':'Accès réservé aux adresses autorisées.'}); return True
        if post and self.headers.get('Origin') != ONLINE.origin:
            self.respond(403,{'error':'Origine refusée.'}); return True
        return None

    def page(self, name):
        text = (ROOT/name).read_text(encoding='utf-8')
        mime = {'html':'text/html','js':'text/javascript','css':'text/css'}[name.split('.')[-1]]
        return self.respond(200, text, mime)

    def do_GET(self):
        path=urlsplit(self.path).path
        if ONLINE:
            if self.online_refused(False):
                return
        elif self.headers.get('Host') != f'127.0.0.1:{PORT}':
            return self.respond(403,{'error':'Hôte refusé.'})
        if path in ASSETS:
            # Fixed allow-list: logos, icon and bundled font only, never a path built from the URL.
            return self.respond(200,(ROOT/'assets'/path.removeprefix('/assets/')).read_bytes(),ASSETS[path])
        if path == '/style.css':
            return self.page('style.css')
        if ONLINE:
            if path in ('/connexion', '/connexion.js'):
                return self.page('connexion.html' if path == '/connexion' else 'connexion.js')
            user = self.current_user()
            if path in ('/', '/ui.js') and not user:
                return self.respond(303, '', 'text/plain', [('Location', '/connexion')])
            if path == '/':
                html = (ROOT/'index.html').read_text(encoding='utf-8')
                html = html.replace('<html lang="fr">', f'<html lang="fr" data-mode="online" data-user="{escape(user)}">', 1)
                return self.respond(200, html, 'text/html')
        files={'/':'index.html','/ui.js':'ui.js'}
        name=files.get(path)
        if not name:
            return self.respond(404,{'error':'Introuvable.'})
        self.page(name)

    def do_POST(self):
        user = None
        if ONLINE:
            if self.online_refused(True):
                return
            if self.path in ('/connexion', '/deconnexion'):
                return self.sign(self.path)
            user = self.current_user()
            if not user:
                return self.respond(401,{'error':'Session expirée. Reconnectez-vous.','login':True})
        elif self.headers.get('Host') != f'127.0.0.1:{PORT}' or self.headers.get('Origin') != f'http://127.0.0.1:{PORT}' or not secrets.compare_digest(self.headers.get('X-Session',''), TOKEN):
            return self.respond(403,{'error':'Session locale non autorisée. Relancez l’outil.'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>12_000_000:
                raise ValueError('Requête trop volumineuse.')
            payload=json.loads(self.rfile.read(size))
            if self.path=='/import':
                return self.respond(200,read_order_file(payload))
            if self.path in ('/customers/list', '/customers/contact', '/customers/match'):
                with READS:
                    action = {'/customers/list': customer_directory, '/customers/contact': customer_contact, '/customers/match': customer_match}[self.path]
                    result = asyncio.run(asyncio.wait_for(action(payload), timeout=180))
                return self.respond(200,result)
            if self.path.startswith('/cart/'):
                with LOCK:
                    result = cart_route(self.path, payload, user)
                return self.respond(200, result)
            if self.path != '/check':
                return self.respond(404,{'error':'Introuvable.'})
            with READS:
                result=asyncio.run(asyncio.wait_for(check(payload),timeout=180))
            self.respond(200,result)
        except KeyRejected as error:
            # Local: the saved cart key no longer works, forget it and ask for a new one.
            # Online: the key belongs to the administrator, who must replace it on the server.
            CARTS.writer.clear()
            text = str(error) if not ONLINE else str(error).split(' Saisissez')[0] + ' Prévenez l’administrateur de l’outil : la clé se remplace dans les réglages du serveur.'
            self.respond(400,{'error':text,'key_rejected':True,'server':bool(ONLINE)})
        except ValueError as error:
            self.respond(400,{'error':str(error)})
        except Exception:
            self.respond(503,{'error':'Contrôle interrompu. Aucune validation disponible. Vérifiez le fichier et la connexion MCP puis réessayez.'})

    def sign(self, path):
        if path == '/deconnexion':
            ACCOUNTS.logout(self.session_token())
            return self.respond(200, {'ok': True}, headers=[('Set-Cookie', online.session_cookie('', 0))])
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 4000:
                raise ValueError
            payload = json.loads(self.rfile.read(size))
        except (ValueError, json.JSONDecodeError):
            return self.respond(400, {'error': 'Requête invalide.'})
        ip = self.client_ip()
        try:
            token = ACCOUNTS.login(payload.get('username'), payload.get('password'), payload.get('code'), ip)
        except PermissionError as error:
            print(f'Connexion refusée pour « {str(payload.get("username",""))[:40]} » depuis {ip}.', flush=True)
            return self.respond(401, {'error': str(error)})
        print(f'Connexion de {payload["username"].strip().lower()} depuis {ip}.', flush=True)
        return self.respond(200, {'ok': True}, headers=[('Set-Cookie', online.session_cookie(token))])


def escape(text):
    return str(text).replace('&','&amp;').replace('"','&quot;').replace('<','&lt;').replace('>','&gt;')


def administer(argv):
    """Command line account management for online mode (never exposed in the page)."""
    import getpass
    accounts = online.Accounts(online.Settings().database())
    if '--list-users' in argv:
        for user in accounts.list():
            print(f"{user['username']:<24} {'actif' if user['active'] else 'désactivé':<10} {'code 2FA' if user['totp'] else 'sans 2FA':<10} {user['created_at']}")
        return
    if '--disable-user' in argv:
        name = argv[argv.index('--disable-user') + 1]
        accounts.disable(name)
        print(f'{name} désactivé, ses sessions sont fermées.')
        return
    name = argv[argv.index('--add-user') + 1]
    password = getpass.getpass(f'Mot de passe pour {name} (12 caractères minimum, saisie masquée) : ')
    if password != getpass.getpass('Retapez-le : '):
        raise SystemExit('Les deux saisies diffèrent. Rien n’a été modifié.')
    secret = accounts.add(name, password, with_totp='--totp' in argv)
    print(f'Compte {name.strip().lower()} prêt. Ses sessions ouvertes ont été fermées.')
    if secret:
        print('Code à usage unique : dans l’application d’authentification du téléphone (Google Authenticator, Microsoft Authenticator…),')
        print(f'ajoutez un compte « FullMoon » avec cette clé de configuration : {secret}')
        print('Ne la conservez nulle part ailleurs.')


if __name__=='__main__' and any(flag in sys.argv for flag in ('--add-user', '--disable-user', '--list-users')):
    administer(sys.argv)
    sys.exit(0)

if __name__=='__main__' and ('--online' in sys.argv or os.environ.get('FULLMOON_MODE') == 'online'):
    start_online(online.Settings())
    server = ThreadingHTTPServer((ONLINE.bind, ONLINE.port), Handler)
    print(f'FullMoon Commandes en ligne sur {ONLINE.origin} (écoute {ONLINE.bind}:{ONLINE.port}).', flush=True)
    server.serve_forever()

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    url=f'http://127.0.0.1:{PORT}/#'+TOKEN
    print('FullMoon Commandes disponible sur ce poste. Gardez cette fenêtre ouverte.',flush=True)
    # Fragment never reaches HTTP logs. It authenticates only this local process.
    if '--no-browser' not in sys.argv:
        webbrowser.open(url)
    else:
        (ROOT/'session.url').write_text(url); (ROOT/'session.url').chmod(0o600)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
