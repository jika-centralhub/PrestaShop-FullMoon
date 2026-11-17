#!/usr/bin/env python3
"""PrestaShop MCP Server — stdio transport.

Reads credentials from .env alongside this file. This installation exposes read-only tools.
Required variables: PS_SHOP_URL, PS_API_KEY

Usage: registered in ~/.claude/settings.json as an MCP server.
Each project has its own .env — run Claude Code from the project directory.
"""
from __future__ import annotations

import os
import sys
import contextlib
import io
from pathlib import Path
from typing import Any

# ps_client.py ships alongside this file
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from ps_client import PSClient, PSRequestError, ConfigError, ReadOnlySession

mcp = FastMCP("prestashop")

_client: PSClient | None = None

_ENV_HINT = """
No .env file in the current project directory.

Create a .env file with your PrestaShop WebService API credentials:

  PS_SHOP_URL=https://yourshop.example/
  PS_API_KEY=YOUR_WEBSERVICE_KEY

How to generate the key:
  BO → Advanced Parameters → Webservice → Add key
  Grant permissions: products, combinations, categories,
  product_features, product_options, stock_availables, warehouses,
  manufacturers, languages
  Optional (generic tools ps_query/ps_get_record):
  customers, addresses, orders, order_details, cart_rules, carriers,
  countries, zones, currencies — or any other resources, GET only.
""".strip()


def _ps() -> PSClient:
    """Return (or lazily create) PSClient from .env in CWD."""
    global _client
    if _client is None:
        env_path = Path(__file__).resolve().parent / ".env"
        if not env_path.exists() and not (os.environ.get("PS_SHOP_URL") and os.environ.get("PS_API_KEY")):
            raise ConfigError(f"No .env in {Path.cwd()}\n\n{_ENV_HINT}")
        _client = PSClient.from_env(str(env_path))
    return _client


@contextlib.contextmanager
def _quiet():
    """Redirect PSClient print() to stderr — stdout belongs to MCP protocol."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _run(fn, *args, **kwargs) -> Any:
    with _quiet():
        return fn(*args, **kwargs)


# ── Connection ────────────────────────────────────────────────────────────────

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_verify() -> str:
    """Check connection to the PrestaShop API. Returns shop URL and status."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = _ps().verify()
    return buf.getvalue().strip() if ok else f"✗ Connection failed: {_ps().base}"


# ── Categories ────────────────────────────────────────────────────────────────

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_categories(show_inactive: bool = False) -> list[dict]:
    """List all product categories. Returns [{id, name, active, id_parent}]."""
    return _run(_ps().list_categories, show_inactive=show_inactive)


def ps_create_category(name: str, parent_id: int = 2, active: int = 1) -> str:
    """Create a new category. parent_id=2 is Home. Returns new ID."""
    id_ = _run(_ps().create_category, name, parent_id=parent_id, active=active)
    return f"Created category '{name}' → ID {id_}"


def ps_rename_category(category_id: int, new_name: str) -> str:
    """Rename an existing category."""
    _run(_ps().rename_category, category_id, new_name)
    return f"Category ID {category_id} renamed to '{new_name}'"


def ps_delete_category(category_id: int) -> str:
    """Delete a category. WARNING: permanent. Only use on empty categories."""
    _run(_ps().delete_category, category_id, confirm=True)
    return f"Category ID {category_id} deleted"


# ── Features ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_features() -> list[dict]:
    """List all product features (Catalog → Attributes & Features → Features).
    Returns [{id, name}]."""
    return _run(_ps().list_features)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_feature_values(feature_id: int) -> list[dict]:
    """List all values for a specific feature (e.g. heel heights for feature 4).
    Returns [{id, value}]."""
    return _run(_ps().list_feature_values, feature_id)


def ps_create_feature(name: str) -> str:
    """Create a new product feature. Returns new ID."""
    id_ = _run(_ps().create_feature, name)
    return f"Created feature '{name}' → ID {id_}"


def ps_create_feature_value(feature_id: int, value_name: str) -> str:
    """Create a new value for a feature (e.g. '12 cm' for feature 'Heel height').
    Returns new ID."""
    id_ = _run(_ps().create_feature_value, feature_id, value_name)
    return f"Created feature value '{value_name}' for feature_id={feature_id} → ID {id_}"


def ps_rename_feature(feature_id: int, new_name: str) -> str:
    """Rename a product feature."""
    _run(_ps().rename_feature, feature_id, new_name)
    return f"Feature ID {feature_id} renamed to '{new_name}'"


# ── Attribute Groups ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_attribute_groups() -> list[dict]:
    """List attribute groups (Catalog → Attributes & Features → Attributes).
    NOTE: PS API endpoint is 'product_options', not 'attribute_groups'.
    Returns [{id, name, type, is_color}]."""
    return _run(_ps().list_attribute_groups)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_attribute_values(group_id: int) -> list[dict]:
    """List all values for an attribute group (e.g. all sizes for group 1).
    Returns [{id, name}]."""
    return _run(_ps().list_attribute_values, group_id)


def ps_create_attribute_group(name: str, group_type: str = "select",
                               is_color: int = 0) -> str:
    """Create a new attribute group.
    group_type: 'select' | 'radio' | 'color'
    is_color: 1 for color swatches. Returns new ID."""
    id_ = _run(_ps().create_attribute_group, name,
               group_type=group_type, is_color=is_color)
    return f"Created attribute group '{name}' (type={group_type}) → ID {id_}"


def ps_create_attribute_value(group_id: int, name: str,
                               color_hex: str = "") -> str:
    """Create a new attribute value (e.g. size '38 EU' or color).
    color_hex: '#FF0000' for color swatches (optional). Returns new ID."""
    id_ = _run(_ps().create_attribute_value, group_id, name, color_hex=color_hex)
    return f"Created attribute value '{name}' for group_id={group_id} → ID {id_}"


def ps_rename_attribute_group(group_id: int, new_name: str) -> str:
    """Rename an attribute group."""
    _run(_ps().rename_attribute_group, group_id, new_name)
    return f"Attribute group ID {group_id} renamed to '{new_name}'"


# ── Products ──────────────────────────────────────────────────────────────────

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_products(limit: int = 20, offset: int = 0) -> list[dict]:
    """List products with pagination.
    Returns [{id, reference, name, price, active}]."""
    return _run(_ps().list_products, limit=limit, offset=offset)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_find_product(reference: str) -> dict | None:
    """Find a product by its reference/SKU (e.g. 'ADORE-708').
    Returns {id, reference, name, price, active} or null."""
    return _run(_ps().find_product, reference)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_get_product(product_id: int) -> dict:
    """Get full product details by ID.
    Returns {id, reference, name, price, active, id_category_default, category_ids}."""
    return _run(_ps().get_product, product_id)


def ps_set_product_active(product_id: int, active: int) -> str:
    """Activate (active=1) or hide (active=0) a product."""
    _run(_ps().set_product_active, product_id, active)
    return f"Product ID {product_id} → {'active' if active else 'hidden'}"


def ps_update_product_price(product_id: int, price: float) -> str:
    """Update product price (gross, in shop currency)."""
    _run(_ps().update_product_price, product_id, price)
    return f"Product ID {product_id} price → {price:.2f}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_combinations(product_id: int) -> list[dict]:
    """List all combinations (variants) of a product.
    Returns [{id, reference, price_impact, quantity, attribute_ids}]."""
    return _run(_ps().list_combinations, product_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_get_combination(combination_id: int) -> dict:
    """Get full details of a single combination by ID.
    Returns {id, id_product, reference, ean13, price_impact, quantity, default_on, attribute_ids, image_ids}."""
    return _run(_ps().get_combination, combination_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_find_combination(reference: str) -> dict | None:
    """Find a combination by its reference/SKU (e.g. 'ADO1002DP/W-BFA/M').
    Returns {id, id_product, reference, ean13, price_impact, quantity, default_on, attribute_ids, image_ids} or null."""
    return _run(_ps().find_combination, reference)


def ps_update_combination(combination_id: int,
                           reference: str = "",
                           ean13: str = "",
                           price_impact: float | None = None,
                           default_on: int | None = None) -> str:
    """Update fields of an existing combination. Pass only the fields you want to change.
    price_impact: net price delta (0 = same as product price).
    default_on: 1 to set as default combination for its product."""
    _run(_ps().update_combination,
         combination_id,
         reference=reference or None,
         ean13=ean13 or None,
         price_impact=price_impact,
         default_on=default_on)
    return f"Combination ID {combination_id} updated"


def ps_assign_combination_image(combination_id: int, image_id: int) -> str:
    """Assign a product image to a combination (links the gallery image to this variant).
    Get image_id from ps_get_product_images. Get combination_id from ps_list_combinations."""
    _run(_ps().assign_combination_image, combination_id, image_id)
    return f"Combination ID {combination_id} → image ID {image_id} assigned"


# ── Stock ─────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_get_stock(product_id: int, combination_id: int = 0) -> list[dict]:
    """Get stock levels for a product (all combinations or specific one).
    Returns [{id, combination_id, quantity}].
    Use stock id from result with ps_update_stock."""
    return _run(_ps().get_stock, product_id, combination_id=combination_id)


def ps_update_stock(stock_id: int, quantity: int) -> str:
    """Set stock quantity for a stock_available record.
    Get stock_id first via ps_get_stock."""
    _run(_ps().update_stock, stock_id, quantity)
    return f"Stock ID {stock_id} quantity → {quantity}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_warehouse_stocks(warehouse_id: int) -> list[dict]:
    """List all stock records for a specific warehouse (endpoint /stocks).
    Returns [{id, id_warehouse, id_product, id_product_attribute, ean13, physical_quantity, usable_quantity}].
    Use warehouse_id from ps_list_warehouses."""
    return _run(_ps().list_warehouse_stocks, warehouse_id)


def ps_create_warehouse_stock(warehouse_id: int, product_id: int,
                               combination_id: int, quantity: int) -> str:
    """Create a new stock record in /stocks for a product/combination in a warehouse.
    Use when the combination has no stock record yet for this warehouse.
    Returns new stock record ID."""
    new_id = _run(_ps().create_warehouse_stock, warehouse_id, product_id, combination_id, quantity)
    return f"Created warehouse stock ID {new_id} (warehouse={warehouse_id} product={product_id} combo={combination_id} qty={quantity})"


def ps_update_warehouse_stock(stock_id: int, quantity: int) -> str:
    """Update physical_quantity and usable_quantity of a /stocks record.
    Get stock_id from ps_list_warehouse_stocks."""
    _run(_ps().update_warehouse_stock, stock_id, quantity)
    return f"Warehouse stock ID {stock_id} → qty {quantity}"


# ── Reference data ────────────────────────────────────────────────────────────

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_warehouses() -> list[dict]:
    """List warehouses (PShow Advanced Stock plugin).
    Returns [{id, name, reference}]."""
    return _run(_ps().list_warehouses)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_manufacturers() -> list[dict]:
    """List manufacturers / brands.
    Returns [{id, name, active}]."""
    return _run(_ps().list_manufacturers)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_languages() -> list[dict]:
    """List shop languages.
    Returns [{id, name, iso, active}]."""
    return _run(_ps().list_languages)


# ── Generic read-only API access ──────────────────────────────────────────────
# Covers EVERY resource the API key grants access to (customers, addresses,
# orders, cart_rules, carriers, ...). Strictly GET — no generic write tools.

import xml.etree.ElementTree as ET

# Never expose these fields to the LLM, regardless of resource.
_SENSITIVE_FIELDS = {"passwd", "secure_key", "reset_password_token", "api_key", "checksum"}


def _el_value(el: ET.Element) -> str:
    """Resolve element text; multilang nodes → shop default language (fallback: first)."""
    langs = el.findall("language")
    if langs:
        for lang in langs:
            if lang.get("id") == _ps().lang_id:
                return (lang.text or "").strip()
        return (langs[0].text or "").strip()
    return (el.text or "").strip()


def _record_to_dict(record: ET.Element) -> dict:
    """Flatten one API record element into a dict. Redacts sensitive fields,
    resolves multilang values, parses <associations> into nested lists."""
    out: dict[str, Any] = {}
    if record.get("id"):
        out["id"] = record.get("id")
    for child in record:
        tag = child.tag
        if tag in _SENSITIVE_FIELDS:
            continue
        if tag == "associations":
            out["associations"] = {
                group.tag: [_record_to_dict(item) for item in group]
                for group in child
            }
            continue
        out[tag] = _el_value(child)
    return out


def _query(resource: str, filters: dict[str, str] | None = None,
           fields: list[str] | None = None, sort: str = "",
           limit: int = 50, offset: int = 0,
           date_filter: bool = False) -> list[dict]:
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("Use limit between 1 and 200 and a nonnegative offset.")
    params: dict[str, str] = {
        "display": f"[{','.join(fields)}]" if fields else "full",
        "limit": f"{offset},{limit}",
    }
    if sort:
        params["sort"] = sort if sort.startswith("[") else f"[{sort}]"
    if date_filter:
        params["date"] = "1"
    for key, val in (filters or {}).items():
        val = str(val)
        params[f"filter[{key}]"] = val if val.startswith(("[", "%", "<", ">")) else f"[{val}]"
    root = _run(_ps()._get, resource, params=params)
    if len(root) == 0:
        return []
    return [_record_to_dict(item) for item in root[0]]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_api_resources() -> list[dict]:
    """List all API resources this key has permission for (GET /api/).
    Returns [{resource, methods}] — use resource names with ps_query / ps_get_record / ps_schema."""
    root = _run(_ps()._get, "")
    api_node = root.find("api")
    if api_node is None:
        return []
    out = []
    for child in api_node:
        if child.tag not in ReadOnlySession.RESOURCES:
            continue
        methods = [m for m in ("get", "put", "post", "delete") if child.get(m) == "true"]
        out.append({"resource": child.tag, "methods": methods or ["get"]})
    return sorted(out, key=lambda r: r["resource"])


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_schema(resource: str) -> dict:
    """Get the field schema of any API resource (?schema=synopsis).
    Returns {field: {format, required, multilang}} — check this before filtering
    an unfamiliar resource with ps_query."""
    root = _run(_ps()._get, resource, params={"schema": "synopsis"})
    if len(root) == 0:
        return {}
    schema: dict[str, dict] = {}
    for field in root[0]:
        info = {k: v for k, v in field.attrib.items() if not k.startswith("{")}
        if field.find("language") is not None:
            info["multilang"] = "true"
        schema[field.tag] = info
    return schema


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_query(resource: str, filters: dict[str, str] | None = None,
             fields: list[str] | None = None, sort: str = "",
             limit: int = 50, offset: int = 0,
             date_filter: bool = False) -> list[dict]:
    """Read-only query on ANY PrestaShop API resource (customers, addresses, orders,
    cart_rules, carriers, countries, ...). Discover resources with ps_api_resources.

    filters: {field: value}. Value syntax (PrestaShop WebService):
      exact:      {"id_customer": "42"}
      LIKE:       {"email": "%[gmail]%"}
      OR list:    {"current_state": "[2|3]"}
      interval:   {"id": "[1,99]"}
      date range: {"date_add": "[2026-01-01,2026-06-30]"} + date_filter=True
    fields: trim output, e.g. ["id","email","lastname"] (default: full records).
    sort: e.g. "id_DESC" or "date_add_ASC".
    Sensitive fields (passwd, secure_key, tokens) are always redacted."""
    return _query(resource, filters, fields, sort, limit, offset, date_filter)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_get_record(resource: str, record_id: int) -> dict:
    """Get one full record of ANY API resource by ID, including associations
    (e.g. order with its order_rows). Sensitive fields are redacted."""
    root = _run(_ps()._get, resource, record_id)
    if len(root) == 0:
        return {}
    return _record_to_dict(root[0])


# Full PS WebService resource map — static guide so the LLM picks the right
# resource without guessing. Actual availability depends on the API key
# (check ps_api_resources).
_RESOURCE_GUIDE: dict[str, dict[str, str]] = {
    "Customers & addresses": {
        "customers": "customers (account data, default group, newsletter)",
        "addresses": "customer/supplier addresses (filter by id_customer)",
        "groups": "customer groups (group discounts, net/gross prices)",
        "guests": "guests (sessions of not-logged-in visitors)",
        "customer_threads": "customer message threads (customer service)",
        "customer_messages": "messages in threads (filter by id_customer_thread)",
        "messages": "order messages",
    },
    "Orders": {
        "orders": "orders (current_state, total_paid_tax_incl, associations→order_rows)",
        "order_details": "order line items (filter by id_order)",
        "order_histories": "order status change history",
        "order_states": "order status dictionary (id→name)",
        "order_payments": "payments (order_reference, amount, payment_method)",
        "order_invoices": "invoices",
        "order_slip": "credit slips / returns",
        "order_carriers": "order shipments (tracking_number)",
        "order_cart_rules": "discounts used in an order (filter by id_order)",
        "carts": "carts, including abandoned (associations→cart_rows)",
    },
    "Discounts & prices": {
        "cart_rules": "voucher / discount codes (conditions, dates, limits)",
        "specific_prices": "price reductions per product/group/country/quantity",
        "specific_price_rules": "catalog price rules",
        "price_ranges": "shipping price ranges",
        "taxes": "tax rates",
        "tax_rules": "tax rules (country↔rate)",
        "tax_rule_groups": "rule groups (assigned to products)",
    },
    "Catalog": {
        "products": "products", "combinations": "combinations (variants)",
        "categories": "categories", "manufacturers": "brands",
        "suppliers": "suppliers", "product_suppliers": "product↔supplier links",
        "product_features": "features", "product_feature_values": "feature values",
        "product_options": "attribute groups", "product_option_values": "attribute values",
        "tags": "product tags", "attachments": "product attachments",
        "customizations": "customizations", "product_customization_fields": "customization fields",
        "images": "images — do NOT use ps_query, use ps_product_images",
        "image_types": "image types/sizes",
        "search": "search — do NOT use ps_query, use ps_search",
    },
    "Stock & warehouse": {
        "stock_availables": "available stock (quantity per product/combination/shop)",
        "stocks": "physical stock per warehouse", "warehouses": "warehouses",
        "warehouse_product_locations": "product locations within a warehouse",
        "stock_movements": "stock movements (history)",
        "stock_movement_reasons": "movement reason dictionary",
        "supply_orders": "supplier orders (+ _details, _histories, _states, _receipt_histories)",
    },
    "Shipping & localization": {
        "carriers": "carriers", "deliveries": "delivery price lists (zone×weight×price)",
        "weight_ranges": "weight ranges", "zones": "geographic zones",
        "countries": "countries", "states": "regions/states", "currencies": "currencies",
    },
    "Shop & configuration": {
        "configurations": "the whole shop configuration (name→value pairs, e.g. PS_SHOP_EMAIL)",
        "translated_configurations": "multilingual configuration",
        "shops": "shops (multistore)", "shop_groups": "shop groups",
        "shop_urls": "shop domains", "stores": "physical stores (locations)",
        "languages": "languages", "employees": "BO employees (passwords redacted)",
        "contacts": "contact departments", "content_management_system": "CMS pages (content)",
    },
}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_resource_guide() -> dict:
    """Map of ALL PrestaShop WebService resources with descriptions, grouped by domain.
    Use to pick the right resource for ps_query — e.g. 'customer discounts'
    → cart_rules / order_cart_rules / specific_prices. Availability depends on the
    API key: cross-check with ps_api_resources."""
    return {group: {name: description for name, description in entries.items()
                    if name in ReadOnlySession.RESOURCES}
            for group, entries in _RESOURCE_GUIDE.items()}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_search(query: str) -> dict:
    """Full-text shop search (resource 'search' — different protocol than ps_query).
    Returns {resource_type: [ids]} (e.g. {'products': [12, 15]}) — hydrate the IDs
    with ps_get_record / ps_get_product."""
    root = _run(_ps()._get, "search",
                params={"query": query, "language": _ps().lang_id})
    out: dict[str, list[str]] = {}
    for group in root:
        ids = [item.get("id") for item in group if item.get("id")]
        if ids:
            out[group.tag] = ids
    return out or {"info": [f"no results for '{query}'"]}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_product_images(product_id: int) -> list[dict]:
    """List image declinations of a product (resource 'images' returns binary data,
    so ps_query does NOT work on it). Returns [{image_id, url}] — image_id is what
    ps_assign_combination_image expects."""
    root = _run(_ps()._get, f"images/products/{product_id}")
    out = []
    for decl in root.iter("declination"):
        href = next((v for k, v in decl.attrib.items() if k.endswith("href")), "")
        out.append({"image_id": decl.get("id"), "url": href})
    return out


# ── Customers / Orders / Discounts (convenience) ──────────────────────────────

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_find_customer(query: str, limit: int = 20) -> list[dict]:
    """Find customers by email fragment or lastname (case-insensitive LIKE).
    Returns [{id, firstname, lastname, email, active, newsletter, date_add}]."""
    field = "email" if "@" in query or "." in query else "lastname"
    return _query("customers", {field: f"%[{query}]%"},
                  fields=["id", "firstname", "lastname", "email", "active",
                          "newsletter", "date_add"], limit=limit)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_get_customer(customer_id: int) -> dict:
    """Get full customer record plus their addresses.
    Returns {customer: {...}, addresses: [{alias, address1, postcode, city, phone, ...}]}."""
    customer = ps_get_record("customers", customer_id)
    addresses = _query("addresses", {"id_customer": str(customer_id)})
    return {"customer": customer, "addresses": addresses}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_orders(customer_id: int = 0, state_id: int = 0, reference: str = "",
                   date_from: str = "", date_to: str = "", limit: int = 20) -> list[dict]:
    """List orders, newest first. All filters optional and combinable.
    state_id: order status ID (check ps_query('order_states', fields=['id','name'])).
    date_from/date_to: 'YYYY-MM-DD' (both required to filter by date)."""
    filters: dict[str, str] = {}
    if customer_id:
        filters["id_customer"] = str(customer_id)
    if state_id:
        filters["current_state"] = str(state_id)
    if reference:
        filters["reference"] = reference
    date_filter = bool(date_from and date_to)
    if date_filter:
        filters["date_add"] = f"[{date_from},{date_to}]"
    return _query("orders", filters,
                  fields=["id", "reference", "id_customer", "current_state",
                          "payment", "total_paid_tax_incl", "date_add"],
                  sort="id_DESC", limit=limit, date_filter=date_filter)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_get_order(order_id: int) -> dict:
    """Get full order details by ID, including order_rows (products, quantities,
    prices) in associations."""
    return ps_get_record("orders", order_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def ps_list_cart_rules(code: str = "", active_only: bool = True, limit: int = 50) -> list[dict]:
    """List discounts / voucher codes (cart rules).
    code: fragment of the voucher code (LIKE). Returns id, code, name, reduction
    fields, validity dates, quantity — enough to verify a customer's discount."""
    filters: dict[str, str] = {}
    if code:
        filters["code"] = f"%[{code}]%"
    if active_only:
        filters["active"] = "1"
    return _query("cart_rules", filters,
                  fields=["id", "code", "name", "description", "reduction_percent",
                          "reduction_amount", "free_shipping", "date_from", "date_to",
                          "quantity", "quantity_per_user", "active"],
                  sort="id_DESC", limit=limit)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
