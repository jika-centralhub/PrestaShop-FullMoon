"""PrestaShop WebService API client.

See README.md for full usage documentation.

Usage:
    from ps_client import PSClient
    ps = PSClient.from_env()
    ps.verify()
    ps.list_categories()
"""

from __future__ import annotations

import re
import datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import requests


# ── Exceptions ────────────────────────────────────────────────────────────────

class PSRequestError(RuntimeError):
    def __init__(self, status: int, method: str, url: str, body: str):
        self.status = status
        snippet = body[:600]
        super().__init__(f"PrestaShop API HTTP {status}")


class ConfigError(RuntimeError):
    pass


# ── .env loader ───────────────────────────────────────────────────────────────

def _load_env(path: str = ".env") -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            f".env not found at {p.resolve()}.\n"
            "Required variables: PS_SHOP_URL, PS_API_KEY"
        )
    data: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        data[key.strip()] = val.strip().strip('"').strip("'")
    return data


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    pl = str.maketrans(
        "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ",
        "acelnoszzACELNOSZZ"
    )
    return re.sub(r"[^a-z0-9-]+", "-", text.lower().translate(pl)).strip("-")


def _cdata(value: Any) -> str:
    # ']]>' inside a value would end the CDATA section early (broken/injected XML)
    # — the standard trick: split into two CDATA sections
    return "<![CDATA[" + str(value).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _lang_nodes(value: str, lang_ids: list[str]) -> str:
    return "".join(f'<language id="{lid}">{_cdata(value)}</language>' for lid in lang_ids)


# Read-only fields — PS rejects a PUT if they are present
_READONLY = [
    "level_depth", "nb_products_recursive", "nb_products",
    "date_add", "date_upd", "position_in_category",
]


def _strip_readonly(xml_str: str) -> str:
    for field in _READONLY:
        xml_str = re.sub(
            rf"<{field}>.*?</{field}>", "", xml_str, flags=re.DOTALL
        )
    return xml_str


def _get_id_from_root(root: ET.Element) -> int | None:
    """Extract the ID from a POST response (first nested element)."""
    for child in root:
        id_el = child.find("id")
        if id_el is not None and id_el.text:
            try:
                return int(id_el.text.strip())
            except ValueError:
                pass
    return None


def _lang_text(el: ET.Element, tag: str, lang_id: str | None = None) -> str:
    if lang_id:
        node = el.find(f".//{tag}/language[@id='{lang_id}']")
        if node is not None:
            return (node.text or "").strip()
    node = el.find(f".//{tag}/language")
    if node is None:
        # single-language resource, or the API returned a flat value (language param in GET)
        node = el.find(f".//{tag}")
    return (node.text or "").strip() if node is not None else ""


# ── Logger ────────────────────────────────────────────────────────────────────

def _log(action: str, resource: str, **meta: Any) -> None:
    parts = [
        datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        action.ljust(7),
        resource.ljust(20),
    ]
    parts += [f"{k}={v}" for k, v in meta.items()]
    line = "  ".join(parts) + "\n"
    try:
        log_dir = Path.home() / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "ps-rest-client.log", "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


# ── PSClient ──────────────────────────────────────────────────────────────────


class ReadOnlySession(requests.Session):
    """Restrict all authenticated traffic to approved read-only shop resources."""

    RESOURCES = {
        "products", "combinations", "categories", "manufacturers", "suppliers",
        "product_suppliers", "product_features", "product_feature_values",
        "product_options", "product_option_values", "stock_availables", "stocks",
        "warehouses", "languages", "orders", "order_details", "order_states",
        "order_histories", "order_payments", "order_invoices", "order_slip",
        "order_carriers", "order_cart_rules", "customers", "addresses",
        "cart_rules", "carriers", "countries", "zones", "currencies", "taxes",
        "tax_rules", "tax_rule_groups", "shops", "shop_groups", "search", "images",
    }

    def request(self, method, url, **kwargs):
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if method.upper() != "GET":
            raise ValueError("Read-only connection: only GET is allowed.")
        if parsed.scheme != "https" or parsed.netloc != "fullmoon-distribution.fr":
            raise ValueError("Destination outside the authorized shop.")
        if parsed.query or parsed.fragment or not re.fullmatch(r"/api(?:/[a-z_]+(?:/[0-9]+|/products/[0-9]+)?)?/?", parsed.path):
            raise ValueError("Invalid API resource path.")
        resource = parsed.path.removeprefix("/api").strip("/").split("/")[0]
        if resource and resource not in self.RESOURCES:
            raise ValueError("Resource outside the authorized read-only scope.")
        if any(str(k).lower() in {"ws_key", "ps_method", "method", "authorization"} for k in (kwargs.get("params") or {})):
            raise ValueError("Authentication or method overrides are forbidden.")
        kwargs["allow_redirects"] = False
        try:
            response = super().request(method, url, **kwargs)
        except requests.RequestException:
            raise RuntimeError("Shop connection failed. Check network and TLS.") from None
        if 300 <= response.status_code < 400:
            raise RuntimeError("Shop redirect rejected. Check the configured URL.")
        return response

class PSClient:
    """
    PrestaShop Legacy WebService API (XML) client.

    Zasoby API:
      categories            — product categories
      product_features      — product features
      product_options       — attribute groups (confusing — this is NOT attribute_groups!)
      product_option_values — attribute values
      products              — products
      combinations          — combinations
      manufacturers         — producenci / marki
      stock_availables      — stock levels
      warehouses            — warehouses
      languages             — shop languages
    """

    TIMEOUT = 30  # seconds per HTTP request — without it a hung shop blocks the MCP tool forever

    def __init__(self, shop_url: str, api_key: str, lang_id: str | None = None):
        self.base = shop_url.rstrip("/") + "/api"
        self.auth = (api_key, "")
        self._lang_id = lang_id  # None → first active shop language (lang_id property)
        self._lang_ids_cache: list[str] | None = None
        self._session = ReadOnlySession()
        self._session.auth = self.auth

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "PSClient":
        env = _load_env(env_path)
        url = env.get("PS_SHOP_URL", "")
        key = env.get("PS_API_KEY", "")
        if not url or not key:
            raise ConfigError("PS_SHOP_URL and PS_API_KEY must be set in the .env file")
        # PS_LANG_ID (optional) forces the read language; without it, the first active shop language
        return cls(url, key, lang_id=env.get("PS_LANG_ID") or None)

    # ── Shop languages — fetched from the API, no hardcoded IDs ──────────────

    @property
    def lang_id(self) -> str:
        """Read language (language param in GET): PS_LANG_ID from .env or the first active one."""
        if self._lang_id is None:
            self._lang_id = self.lang_ids[0]
        return self._lang_id

    @property
    def lang_ids(self) -> list[str]:
        """IDs of active shop languages — one request, cached for the client's lifetime."""
        if self._lang_ids_cache is None:
            try:
                # raw request instead of _get() — _get uses self.lang_id, which would recurse
                r = self._session.get(f"{self.base}/languages",
                                      params={"display": "full"}, timeout=self.TIMEOUT)
                r.raise_for_status()
                ids = []
                for lang in ET.fromstring(r.content).findall(".//language"):
                    id_el = lang.find("id")
                    if id_el is None or not (id_el.text or "").strip():
                        continue
                    active_el = lang.find("active")
                    if active_el is not None and (active_el.text or "1").strip() == "0":
                        continue
                    ids.append(id_el.text.strip())
                self._lang_ids_cache = ids or ["1"]
            except Exception:
                # Justified fallback: the API key may lack permission for the
                # 'languages' resource — 1 and 2 are the IDs of a typical bilingual PS install.
                self._lang_ids_cache = ["1", "2"]
        return self._lang_ids_cache

    def _lang_nodes(self, value: str) -> str:
        """<language> nodes for all active shop languages."""
        return _lang_nodes(value, self.lang_ids)

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _get(self, resource: str, id_: int | None = None,
             params: dict | None = None) -> ET.Element:
        url = f"{self.base}/{resource}"
        if id_ is not None:
            url += f"/{id_}"
        p = {"language": self.lang_id, **(params or {})}
        r = self._session.get(url, params=p, timeout=self.TIMEOUT)
        if not r.ok:
            raise PSRequestError(r.status_code, "GET", url, r.text)
        return ET.fromstring(r.content)

    def _post(self, resource: str, xml_body: str) -> ET.Element:
        url = f"{self.base}/{resource}"
        r = self._session.post(
            url, data=xml_body.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
            timeout=self.TIMEOUT
        )
        _log("CREATE", resource, status=r.status_code)
        if not r.ok:
            raise PSRequestError(r.status_code, "POST", url, r.text)
        return ET.fromstring(r.content)

    def _put(self, resource: str, id_: int, xml_body: str) -> ET.Element:
        url = f"{self.base}/{resource}/{id_}"
        xml_body = _strip_readonly(xml_body)
        r = self._session.put(
            url, data=xml_body.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
            timeout=self.TIMEOUT
        )
        _log("UPDATE", resource, id=id_, status=r.status_code)
        if not r.ok:
            raise PSRequestError(r.status_code, "PUT", url, r.text)
        return ET.fromstring(r.content)

    def _delete(self, resource: str, id_: int) -> None:
        url = f"{self.base}/{resource}/{id_}"
        r = self._session.delete(url, timeout=self.TIMEOUT)
        _log("DELETE", resource, id=id_, status=r.status_code)
        if not r.ok:
            raise PSRequestError(r.status_code, "DELETE", url, r.text)

    def _fetch_xml(self, resource: str, id_: int) -> str:
        """Fetch the raw XML of a resource for modification and PUT."""
        url = f"{self.base}/{resource}/{id_}"
        r = self._session.get(url, timeout=self.TIMEOUT)
        if not r.ok:
            raise PSRequestError(r.status_code, "GET", url, r.text)
        return r.text

    def _set_field(self, xml: str, field: str, value: str) -> str:
        """Replace the value of a simple CDATA field."""
        # lambda instead of a replacement string — a value with '\' or '\g' must not be
        # interpreted as an re.sub group reference
        return re.sub(
            rf"<{field}><!\[CDATA\[.*?\]\]></{field}>",
            lambda m: f"<{field}>{_cdata(value)}</{field}>",
            xml, flags=re.DOTALL
        )

    def _set_lang_field(self, xml: str, field: str, value: str) -> str:
        """Replace all <language> nodes inside a given tag."""
        def _replace_langs(m: re.Match) -> str:
            inner = re.sub(
                r'<language id="(\d+)">(?:<!\[CDATA\[)?.*?(?:\]\]>)?</language>',
                lambda lm: f'<language id="{lm.group(1)}"><![CDATA[{value}]]></language>',
                m.group(1)
            )
            return f"<{field}>{inner}</{field}>"
        return re.sub(
            rf"<{field}>(.*?)</{field}>",
            _replace_langs, xml, flags=re.DOTALL
        )

    # ── Diagnostics ───────────────────────────────────────────────────────

    def verify(self) -> bool:
        """Test the connection and print a shop summary."""
        r = self._session.get(self.base, timeout=self.TIMEOUT)
        if r.ok:
            root = ET.fromstring(r.content)
            api_node = root.find("api")
            resources = [child.tag for child in api_node] if api_node is not None else []
            print(f"✓ Connected: {self.base}")
            print(f"  API resources: {len(resources)}")
            return True
        else:
            print(f"✗ Connection error HTTP {r.status_code}: {r.text[:200]}")
            return False

    def inspect(self, resource: str, id_: int) -> None:
        """Print the formatted XML of any resource."""
        xml = self._fetch_xml(resource, id_)
        try:
            root = ET.fromstring(xml.encode())
            ET.indent(root)
            print(ET.tostring(root, encoding="unicode"))
        except ET.ParseError:
            print(xml)

    # ── Categories ────────────────────────────────────────────────────────

    def list_categories(self, show_inactive: bool = False) -> list[dict]:
        """List all categories."""
        root = self._get("categories", params={"display": "full"})
        seen, cats = set(), []
        for c in root.findall(".//category"):
            id_el = c.find("id")
            if id_el is None:
                continue
            id_ = int(id_el.text)
            if id_ in seen:
                continue
            name = _lang_text(c, "name", self.lang_id)
            if not name:
                continue
            active = (c.find("active").text or "0") if c.find("active") is not None else "1"
            if not show_inactive and active == "0":
                continue
            seen.add(id_)
            cats.append({
                "id": id_,
                "name": name,
                "active": active,
                "id_parent": (c.find("id_parent").text or "0") if c.find("id_parent") is not None else "0",
            })
        cats.sort(key=lambda x: x["id"])
        for c in cats:
            print(f"  ID {c['id']:>3}  parent={c['id_parent']:>3}  {c['name']}")
        return cats

    def create_category(self, name: str, parent_id: int = 2,
                        active: int = 1) -> int:
        """Create a new category. Returns the ID."""
        slug = _slugify(name)
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<prestashop>
<category>
  <id_parent><![CDATA[{parent_id}]]></id_parent>
  <active><![CDATA[{active}]]></active>
  <name>{self._lang_nodes(name)}</name>
  <link_rewrite>{self._lang_nodes(slug)}</link_rewrite>
  <description>{self._lang_nodes('')}</description>
  <meta_title>{self._lang_nodes('')}</meta_title>
</category>
</prestashop>"""
        root = self._post("categories", xml)
        id_ = _get_id_from_root(root)
        print(f"✓ Category '{name}' → ID {id_}  slug={slug}")
        return id_

    def rename_category(self, id_: int, new_name: str) -> None:
        """Rename a category."""
        xml_str = self._fetch_xml("categories", id_)
        root = ET.fromstring(xml_str.encode())
        cat = root.find('.//category')
        for field, val in [('name', new_name), ('link_rewrite', _slugify(new_name))]:
            parent = cat.find(field) if cat is not None else None
            if parent is not None:
                for lang in parent.findall('language'):
                    lang.text = val
        updated = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode')
        self._put("categories", id_, updated)
        print(f"✓ Category ID {id_} → '{new_name}'")

    def delete_category(self, id_: int, confirm: bool = False) -> None:
        """Delete a category. Requires confirm=True."""
        if not confirm:
            raise ValueError("Pass confirm=True to delete the category.")
        self._delete("categories", id_)
        print(f"✓ Category ID {id_} deleted")

    # ── Features ──────────────────────────────────────────────────────────

    def list_features(self) -> list[dict]:
        """List product features (Catalog → Attributes & Features → Features)."""
        root = self._get("product_features", params={"display": "full"})
        features = []
        for f in root.findall(".//product_feature"):
            id_el = f.find("id")
            if id_el is None:
                continue
            features.append({
                "id": int(id_el.text),
                "name": _lang_text(f, "name", self.lang_id),
            })
        features.sort(key=lambda x: x["id"])
        for f in features:
            print(f"  ID {f['id']:>2}  {f['name']}")
        return features

    def create_feature(self, name: str) -> int:
        """Add a new product feature. Returns the ID."""
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<prestashop>
<product_feature>
  <position></position>
  <name>{self._lang_nodes(name)}</name>
</product_feature>
</prestashop>"""
        root = self._post("product_features", xml)
        id_ = _get_id_from_root(root)
        print(f"✓ Feature '{name}' → ID {id_}")
        return id_

    def rename_feature(self, id_: int, new_name: str) -> None:
        """Rename a feature."""
        xml_str = self._fetch_xml("product_features", id_)
        root = ET.fromstring(xml_str.encode())
        feature = root.find('.//product_feature')
        parent = feature.find('name') if feature is not None else None
        if parent is not None:
            for lang in parent.findall('language'):
                lang.text = new_name
        updated = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode')
        self._put("product_features", id_, updated)
        print(f"✓ Feature ID {id_} → '{new_name}'")

    # ── Attribute Groups ─────────────────────────────────────────────────
    # NOTE: the PS API endpoint is "product_options", NOT "attribute_groups"!

    def list_attribute_groups(self) -> list[dict]:
        """List combination attribute groups."""
        root = self._get("product_options", params={"display": "full"})
        groups = []
        for g in root.findall(".//product_option"):
            id_el = g.find("id")
            if id_el is None:
                continue
            type_el = g.find("group_type")
            color_el = g.find("is_color_group")
            groups.append({
                "id": int(id_el.text),
                "name": _lang_text(g, "name", self.lang_id),
                "type": (type_el.text or "") if type_el is not None else "",
                "is_color": (color_el.text or "0") if color_el is not None else "0",
            })
        groups.sort(key=lambda x: x["id"])
        for g in groups:
            print(f"  ID {g['id']:>2}  type={g['type']:<8}  color={g['is_color']}  {g['name']}")
        return groups

    def create_attribute_group(self, name: str, group_type: str = "select",
                                is_color: int = 0) -> int:
        """
        Create an attribute group.
        group_type: 'select' | 'radio' | 'color'
        is_color: 1 for a color type (swatch)
        """
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<prestashop>
<product_option>
  <is_color_group><![CDATA[{is_color}]]></is_color_group>
  <group_type>{_cdata(group_type)}</group_type>
  <position></position>
  <name>{self._lang_nodes(name)}</name>
  <public_name>{self._lang_nodes(name)}</public_name>
</product_option>
</prestashop>"""
        root = self._post("product_options", xml)
        id_ = _get_id_from_root(root)
        print(f"✓ Attribute group '{name}' (type={group_type}) → ID {id_}")
        return id_

    def rename_attribute_group(self, id_: int, new_name: str,
                                group_type: str | None = None,
                                is_color: int | None = None) -> None:
        """Rename an attribute group. Optionally set type and is_color."""
        xml_str = self._fetch_xml("product_options", id_)
        tree = ET.ElementTree(ET.fromstring(xml_str.encode()))
        root = tree.getroot()
        option = root.find('.//product_option')
        # Remove read-only
        for tag in _READONLY:
            el = option.find(tag) if option is not None else None
            if el is not None:
                option.remove(el)
        # Set names in all languages
        for field in ['name', 'public_name']:
            parent = option.find(field) if option is not None else None
            if parent is not None:
                for lang in parent.findall('language'):
                    lang.text = new_name
        # Opcjonalnie typ i kolor
        if group_type and option is not None:
            gt = option.find('group_type')
            if gt is not None:
                gt.text = group_type
        if is_color is not None and option is not None:
            cg = option.find('is_color_group')
            if cg is not None:
                cg.text = str(is_color)
        updated_xml = ET.tostring(root, encoding='unicode', xml_declaration=False)
        updated_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + updated_xml
        self._put("product_options", id_, updated_xml)
        print(f"✓ Attribute group ID {id_} → '{new_name}'")

    # ── Attribute Values ──────────────────────────────────────────────────

    def list_attribute_values(self, group_id: int) -> list[dict]:
        """List attribute values for a given group."""
        root = self._get("product_option_values", params={
            "display": "full",
            "filter[id_attribute_group]": group_id,
        })
        values = []
        for v in root.findall(".//product_option_value"):
            id_el = v.find("id")
            if id_el is None:
                continue
            values.append({
                "id": int(id_el.text),
                "name": _lang_text(v, "name", self.lang_id),
            })
        values.sort(key=lambda x: x["id"])
        for v in values:
            print(f"  ID {v['id']:>4}  {v['name']}")
        return values

    # ── Products ──────────────────────────────────────────────────────────

    def list_products(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """List products (limit, offset)."""
        root = self._get("products", params={
            "display": "full",
            "limit": f"{offset},{limit}",
        })
        products = []
        for p in root.findall(".//product"):
            id_el = p.find("id")
            if id_el is None:
                continue
            ref_el = p.find("reference")
            price_el = p.find("price")
            active_el = p.find("active")
            products.append({
                "id": int(id_el.text),
                "reference": (ref_el.text or "") if ref_el is not None else "",
                "name": _lang_text(p, "name", self.lang_id),
                "price": (price_el.text or "0") if price_el is not None else "0",
                "active": (active_el.text or "1") if active_el is not None else "1",
            })
        products.sort(key=lambda x: x["id"])
        for p in products:
            print(f"  ID {p['id']:>5}  ref={p['reference']:<25}  active={p['active']}  "
                  f"cena={float(p['price']):.2f}  {p['name'][:35]}")
        return products

    def find_product(self, reference: str) -> dict | None:
        """Find a product by reference. Returns a dict or None."""
        root = self._get("products", params={
            "display": "full",
            "filter[reference]": f"[{reference}]",
        })
        prods = root.findall(".//product")
        if not prods:
            print(f"  No product found with reference '{reference}'")
            return None
        p = prods[0]
        id_el = p.find("id")
        result = {
            "id": int(id_el.text),
            "reference": reference,
            "name": _lang_text(p, "name", self.lang_id),
            "price": p.find("price").text or "0",
            "active": (p.find("active").text or "1"),
        }
        print(f"  ID {result['id']}  {result['name']}  cena={float(result['price']):.2f}  active={result['active']}")
        return result

    def set_product_active(self, product_id: int, active: int) -> None:
        """Activate (1) or hide (0) a product."""
        xml = self._fetch_xml("products", product_id)
        xml = self._set_field(xml, "active", str(active))
        self._put("products", product_id, xml)
        print(f"✓ Product ID {product_id} → {'active' if active else 'hidden'}")

    def update_product_price(self, product_id: int, price: float) -> None:
        """Update the product's gross price."""
        xml = self._fetch_xml("products", product_id)
        xml = self._set_field(xml, "price", f"{price:.6f}")
        self._put("products", product_id, xml)
        print(f"✓ Product ID {product_id} → price {price:.2f}")

    # ── Stock ─────────────────────────────────────────────────────────────

    def get_stock(self, product_id: int,
                  combination_id: int = 0) -> list[dict]:
        """Get stock levels for a product (all combinations)."""
        params: dict = {"display": "full", "filter[id_product]": product_id}
        if combination_id:
            params["filter[id_product_attribute]"] = combination_id
        root = self._get("stock_availables", params=params)
        stocks = []
        for s in root.findall(".//stock_available"):
            id_el = s.find("id")
            if id_el is None:
                continue
            combo_el = s.find("id_product_attribute")
            qty_el = s.find("quantity")
            stocks.append({
                "id": int(id_el.text),
                "combination_id": int(combo_el.text) if combo_el is not None else 0,
                "quantity": int(qty_el.text or 0) if qty_el is not None else 0,
            })
        for s in stocks:
            print(f"  stock_id={s['id']:>5}  combo_id={s['combination_id']:>5}  qty={s['quantity']}")
        return stocks

    def update_stock(self, stock_id: int, quantity: int) -> None:
        """Set the quantity in stock_available."""
        xml = self._fetch_xml("stock_availables", stock_id)
        xml = self._set_field(xml, "quantity", str(quantity))
        self._put("stock_availables", stock_id, xml)
        print(f"✓ Stock ID {stock_id} → qty {quantity}")

    # ── Warehouses ────────────────────────────────────────────────────────

    def list_warehouses(self) -> list[dict]:
        """List warehouses (PShowAdvancedStockWarehouses)."""
        try:
            root = self._get("warehouses", params={"display": "full"})
        except PSRequestError as e:
            # PS9 + PShowAdvancedStockWarehouses can return HTTP 500 with a PHP Warning
            # but the response contains data — try to parse it despite the error
            if e.status == 500 and "warehouses" in str(e):
                url = f"{self.base}/warehouses"
                r = self._session.get(url, params={"display": "full"}, timeout=self.TIMEOUT)
                try:
                    root = ET.fromstring(r.content)
                except ET.ParseError:
                    print("⚠ Warehouses — the endpoint returns a PS9 error (PHP Warning). Check the PShowAdvancedStockWarehouses plugin.")
                    return []
            else:
                raise
        warehouses = []
        for w in root.findall(".//warehouse"):
            id_el = w.find("id")
            if id_el is None:
                continue
            name_el = w.find("name")
            ref_el = w.find("reference")
            warehouses.append({
                "id": int(id_el.text),
                "name": (name_el.text or "") if name_el is not None else "",
                "reference": (ref_el.text or "") if ref_el is not None else "",
            })
        for w in warehouses:
            print(f"  ID {w['id']}  ref={w['reference']:<10}  {w['name']}")
        return warehouses

    # ── Manufacturers (Marki) ─────────────────────────────────────────────

    def list_manufacturers(self) -> list[dict]:
        """List manufacturers / brands."""
        root = self._get("manufacturers", params={"display": "full"})
        brands = []
        for m in root.findall(".//manufacturer"):
            id_el = m.find("id")
            if id_el is None:
                continue
            name_el = m.find("name")
            active_el = m.find("active")
            brands.append({
                "id": int(id_el.text),
                "name": (name_el.text or "") if name_el is not None else "",
                "active": (active_el.text or "1") if active_el is not None else "1",
            })
        brands.sort(key=lambda x: x["id"])
        for b in brands:
            print(f"  ID {b['id']:>3}  active={b['active']}  {b['name']}")
        return brands

    # ── Feature Values ──────────────────────────────────────────────────

    def list_feature_values(self, feature_id: int) -> list[dict]:
        """List values of a given product feature."""
        root = self._get("product_feature_values", params={
            "display": "full",
            "filter[id_feature]": feature_id,
        })
        values = []
        for v in root.findall(".//product_feature_value"):
            id_el = v.find("id")
            if id_el is None:
                continue
            values.append({
                "id": int(id_el.text),
                "value": _lang_text(v, "value", self.lang_id),
            })
        values.sort(key=lambda x: x["id"])
        for v in values:
            print(f"  ID {v['id']:>4}  {v['value']}")
        return values

    def create_feature_value(self, feature_id: int, value_name: str) -> int:
        """Add a feature value (e.g. '12 cm' for 'Heel height'). Returns the ID."""
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<prestashop>
<product_feature_value>
  <id_feature><![CDATA[{feature_id}]]></id_feature>
  <custom><![CDATA[0]]></custom>
  <value>{self._lang_nodes(value_name)}</value>
</product_feature_value>
</prestashop>"""
        root = self._post("product_feature_values", xml)
        id_ = _get_id_from_root(root)
        print(f"✓ Feature value '{value_name}' (feature_id={feature_id}) → ID {id_}")
        return id_

    # ── Attribute Values — tworzenie ──────────────────────────────────────

    def create_attribute_value(self, group_id: int, name: str,
                                color_hex: str = "") -> int:
        """Add an attribute value (e.g. size '38 EU' or a color). Returns the ID."""
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<prestashop>
<product_option_value>
  <id_attribute_group><![CDATA[{group_id}]]></id_attribute_group>
  <color>{_cdata(color_hex)}</color>
  <position></position>
  <name>{self._lang_nodes(name)}</name>
</product_option_value>
</prestashop>"""
        root = self._post("product_option_values", xml)
        id_ = _get_id_from_root(root)
        print(f"✓ Attribute value '{name}' (group_id={group_id}) → ID {id_}")
        return id_

    # ── Product — details & combinations ─────────────────────────────────

    def get_product(self, product_id: int) -> dict:
        """Get full product data by ID."""
        root = self._get("products", id_=product_id)
        p = root.find(".//product")
        if p is None:
            raise ValueError(f"Product ID {product_id} not found")
        id_el = p.find("id")
        ref_el = p.find("reference")
        price_el = p.find("price")
        active_el = p.find("active")
        id_cat_el = p.find("id_category_default")
        cats = sorted(
            {el.text for el in p.findall(".//id_category") if el.text and el.text != "0"},
            key=lambda x: int(x)
        )
        result = {
            "id": int(id_el.text),
            "reference": (ref_el.text or "") if ref_el is not None else "",
            "name": _lang_text(p, "name", self.lang_id),
            "price": (price_el.text or "0") if price_el is not None else "0",
            "active": (active_el.text or "1") if active_el is not None else "1",
            "id_category_default": (id_cat_el.text or "0") if id_cat_el is not None else "0",
            "category_ids": cats,
        }
        print(f"  ID {result['id']}  ref={result['reference']}  active={result['active']}  {result['name']}")
        return result

    def list_combinations(self, product_id: int) -> list[dict]:
        """List a product's combinations with attribute IDs and stock."""
        root = self._get("combinations", params={
            "display": "full",
            "filter[id_product]": product_id,
        })
        combos = []
        for c in root.findall(".//combination"):
            id_el = c.find("id")
            if id_el is None:
                continue
            ref_el = c.find("reference")
            price_el = c.find("price")
            qty_el = c.find("quantity")
            attrs = [el.text for el in c.findall(".//id_attribute") if el.text]
            combos.append({
                "id": int(id_el.text),
                "reference": (ref_el.text or "") if ref_el is not None else "",
                "price_impact": (price_el.text or "0") if price_el is not None else "0",
                "quantity": int(qty_el.text or 0) if qty_el is not None else 0,
                "attribute_ids": attrs,
            })
        combos.sort(key=lambda x: x["id"])
        for c in combos:
            print(f"  combo_id={c['id']:>5}  ref={c['reference']:<20}  "
                  f"qty={c['quantity']}  attrs={c['attribute_ids']}")
        return combos

    # ── Languages ─────────────────────────────────────────────────────────

    def list_languages(self) -> list[dict]:
        """List shop languages."""
        root = self._get("languages", params={"display": "full"})
        langs = []
        for l in root.findall(".//language"):
            id_el = l.find("id")
            if id_el is None:
                continue
            name_el = l.find("name")
            iso_el = l.find("iso_code")
            active_el = l.find("active")
            langs.append({
                "id": int(id_el.text),
                "name": (name_el.text or "") if name_el is not None else "",
                "iso": (iso_el.text or "") if iso_el is not None else "",
                "active": (active_el.text or "1") if active_el is not None else "1",
            })
        for l in langs:
            print(f"  ID {l['id']}  iso={l['iso']}  active={l['active']}  {l['name']}")
        return langs

    # ── Combinations — details & update ───────────────────────────────────

    def get_combination(self, combination_id: int) -> dict:
        """Get full combination data by ID."""
        root = self._get("combinations", id_=combination_id)
        c = root.find(".//combination")
        if c is None:
            raise ValueError(f"Combination ID {combination_id} not found")
        def _txt(tag: str) -> str:
            el = c.find(tag)
            return (el.text or "").strip() if el is not None else ""
        attrs = [el.findtext("id") for el in c.findall(".//product_option_values/product_option_value") if el.findtext("id")]
        images = [el.findtext("id") for el in c.findall(".//images/image") if el.findtext("id")]
        result = {
            "id": int(_txt("id")),
            "id_product": int(_txt("id_product") or 0),
            "reference": _txt("reference"),
            "ean13": _txt("ean13"),
            "price_impact": _txt("price"),
            "quantity": int(_txt("quantity") or 0),
            "default_on": _txt("default_on"),
            "attribute_ids": attrs,
            "image_ids": images,
        }
        print(f"  combo_id={result['id']}  ref={result['reference']}  "
              f"qty={result['quantity']}  default={result['default_on']}  images={result['image_ids']}")
        return result

    def find_combination(self, reference: str) -> dict | None:
        """Find a combination by reference. Returns a dict or None."""
        root = self._get("combinations", params={
            "display": "full",
            "filter[reference]": f"[{reference}]",
        })
        combos = root.findall(".//combination")
        if not combos:
            print(f"  No combination found with reference '{reference}'")
            return None
        c = combos[0]
        def _txt(tag: str) -> str:
            el = c.find(tag)
            return (el.text or "").strip() if el is not None else ""
        attrs = [el.findtext("id") for el in c.findall(".//product_option_values/product_option_value") if el.findtext("id")]
        images = [el.findtext("id") for el in c.findall(".//images/image") if el.findtext("id")]
        result = {
            "id": int(_txt("id")),
            "id_product": int(_txt("id_product") or 0),
            "reference": _txt("reference"),
            "ean13": _txt("ean13"),
            "price_impact": _txt("price"),
            "quantity": int(_txt("quantity") or 0),
            "default_on": _txt("default_on"),
            "attribute_ids": attrs,
            "image_ids": images,
        }
        print(f"  combo_id={result['id']}  prod_id={result['id_product']}  "
              f"ref={result['reference']}  qty={result['quantity']}")
        return result

    def update_combination(self, combination_id: int,
                           reference: str | None = None,
                           ean13: str | None = None,
                           price_impact: float | None = None,
                           default_on: int | None = None) -> None:
        """Update combination fields. Pass only the ones you want to change."""
        xml = self._fetch_xml("combinations", combination_id)
        if reference is not None:
            xml = re.sub(r"<reference>.*?</reference>",
                         lambda m: f"<reference>{_cdata(reference)}</reference>", xml, flags=re.DOTALL)
        if ean13 is not None:
            xml = re.sub(r"<ean13>.*?</ean13>",
                         lambda m: f"<ean13>{_cdata(ean13)}</ean13>", xml, flags=re.DOTALL)
        if price_impact is not None:
            xml = re.sub(r"<price>.*?</price>",
                         lambda m: f"<price>{_cdata(f'{price_impact:.6f}')}</price>", xml, flags=re.DOTALL)
        if default_on is not None:
            xml = re.sub(r"<default_on>.*?</default_on>",
                         lambda m: f"<default_on>{_cdata(str(default_on))}</default_on>", xml, flags=re.DOTALL)
        self._put("combinations", combination_id, xml)
        changes = {k: v for k, v in [
            ("reference", reference), ("ean13", ean13),
            ("price_impact", price_impact), ("default_on", default_on)
        ] if v is not None}
        print(f"✓ Combination ID {combination_id} updated: {changes}")

    def assign_combination_image(self, combination_id: int, image_id: int) -> None:
        """Assign a product image (image_id) to a combination."""
        xml = self._fetch_xml("combinations", combination_id)
        image_node = f'<images><image id="{int(image_id)}" /></images>'
        if "<images>" in xml:
            xml = re.sub(r"<images>.*?</images>", lambda m: image_node, xml, flags=re.DOTALL)
        else:
            xml = xml.replace("</associations>", f"  {image_node}\n</associations>")
        self._put("combinations", combination_id, xml)
        print(f"✓ Combination ID {combination_id} → image ID {image_id}")

    # ── Warehouse stocks (/stocks — PShowAdvancedStockWarehouses) ─────────

    def list_warehouse_stocks(self, warehouse_id: int) -> list[dict]:
        """List /stocks records for a given warehouse."""
        root = self._get("stocks", params={
            "display": "full",
            "filter[id_warehouse]": warehouse_id,
            "limit": 5000,
        })
        stocks = []
        for s in root.findall(".//stock"):
            id_el = s.find("id")
            if id_el is None:
                continue
            stocks.append({
                "id":                   int(id_el.text),
                "id_warehouse":         int(s.findtext("id_warehouse") or 0),
                "id_product":           int(s.findtext("id_product") or 0),
                "id_product_attribute": int(s.findtext("id_product_attribute") or 0),
                "ean13":                s.findtext("ean13") or "",
                "physical_quantity":    int(s.findtext("physical_quantity") or 0),
                "usable_quantity":      int(s.findtext("usable_quantity") or 0),
            })
        print(f"  Warehouse {warehouse_id}: {len(stocks)} records")
        return stocks

    def create_warehouse_stock(self, warehouse_id: int, product_id: int,
                               combination_id: int, quantity: int) -> int:
        """Create a /stocks record for a combination in a warehouse. Returns the new ID."""
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">'
            "<stock>"
            f"<id_warehouse>{int(warehouse_id)}</id_warehouse>"
            f"<id_product>{int(product_id)}</id_product>"
            f"<id_product_attribute>{int(combination_id)}</id_product_attribute>"
            f"<physical_quantity>{int(quantity)}</physical_quantity>"
            f"<usable_quantity>{int(quantity)}</usable_quantity>"
            "</stock></prestashop>"
        )
        resp = self._post("stocks", xml)
        id_el = resp.find(".//stock/id")
        if id_el is None:
            id_el = resp.find(".//id")
        new_id = int(id_el.text) if id_el is not None and id_el.text else 0
        print(f"✓ warehouse={warehouse_id} product={product_id} combo={combination_id} qty={quantity} → stock ID {new_id}")
        return new_id

    def update_warehouse_stock(self, stock_id: int, quantity: int) -> None:
        """Set physical_quantity and usable_quantity of a /stocks record."""
        xml = self._fetch_xml("stocks", stock_id)
        xml = self._set_field(xml, "physical_quantity", str(quantity))
        xml = self._set_field(xml, "usable_quantity", str(quantity))
        self._put("stocks", stock_id, xml)
        print(f"✓ Warehouse stock ID {stock_id} → qty {quantity}")
