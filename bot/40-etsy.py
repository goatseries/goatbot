"""Etsy shop order reads, using the owner-authorized Etsy OAuth connection.

Read-only: receipts (orders) and the shop record. Money values are Etsy's
{amount, divisor, currency_code} objects; anything non-USD or malformed makes
the period unavailable, never zero. Etsy fees are not exposed per receipt, so
figures are before Etsy fees, labeled as such in reports.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import requests
from . import config as c, costs, etsy_connect as auth

LOCK = threading.RLock()
_SHOP = {"id": None, "at": 0}
_RECEIPTS_CACHE = {}
MAX_PAGES = 25  # 2500 receipts per window; beyond that the period is reported unavailable


class Unavailable(RuntimeError):
    pass


def available():
    """Connected and configured. False means: stay silent, not 'zero sales'."""
    if not auth.enabled() or not auth.ready():
        return False
    path = os.path.join(os.environ.get('ETSY_DATA_DIR') or os.environ.get('TIKTOK_DATA_DIR', '/data'), 'etsy', 'tokens.json')
    return os.path.exists(path)


def _store_path(name):
    return os.path.join(os.environ.get('ETSY_DATA_DIR') or os.environ.get('TIKTOK_DATA_DIR', '/data'), 'etsy', name)


def _token():
    # Shared with the callback, so refresh never overwrites an in-process new grant.
    with auth.LOCK:
        path = _store_path('tokens.json')
        try:
            with open(path) as f:
                token = json.load(f)
        except Exception:
            raise Unavailable('Etsy connection unavailable') from None
        try:
            deadline = float(token['created_at']) + float(token['expires_in'])
            user_id = str(token['user_id'])
        except Exception:
            raise Unavailable('Etsy authorization invalid') from None
        if time.time() >= deadline - 300:
            try:
                response = requests.post(auth.TOKEN, data={
                    'grant_type': 'refresh_token', 'client_id': auth._keystring(),
                    'refresh_token': token['refresh_token'],
                }, timeout=30)
                response.raise_for_status()
                updated = response.json()
                if not updated.get('access_token') or not updated.get('refresh_token') or not updated.get('expires_in'):
                    raise ValueError()
                if str(updated['access_token']).split('.')[0] != user_id:
                    raise ValueError()  # a different shop account would silently re-scope reporting
            except Exception:
                raise Unavailable('Etsy reauthorization required: /etsyconnect in owner chat') from None
            updated['created_at'] = time.time()
            updated['user_id'] = user_id
            auth._write(path, updated)
            token = updated
        return token['access_token'], user_id


def _get(path, params=None):
    bearer, _ = _token()
    response = requests.get(auth.API + path, params=params, headers={
        'x-api-key': auth.api_key_header(), 'Authorization': 'Bearer ' + bearer,
    }, timeout=30)
    if response.status_code != 200:
        raise Unavailable('Etsy read failed: HTTP ' + str(response.status_code))
    return response.json()


def _shop_id():
    if _SHOP["id"] and time.time() - _SHOP["at"] < 3600:
        return _SHOP["id"]
    with LOCK:
        if _SHOP["id"] and time.time() - _SHOP["at"] < 3600:
            return _SHOP["id"]
        cached = None
        try:
            with open(_store_path('shop.json')) as f:
                cached = json.load(f)
        except Exception:
            pass
        if cached and cached.get('shop_id'):
            _SHOP.update(id=int(cached['shop_id']), at=time.time())
            return _SHOP["id"]
        _, user_id = _token()
        data = _get(f'/v3/application/users/{user_id}/shops')
        shops = data.get('results') or ([data] if data.get('shop_id') else [])
        mine = [s for s in shops if s.get('shop_id') and str(s.get('user_id', user_id)) == user_id]
        if len(mine) != 1:
            raise Unavailable('Etsy shop unavailable')
        auth._write(_store_path('shop.json'), {'shop_id': int(mine[0]['shop_id'])})
        _SHOP.update(id=int(mine[0]['shop_id']), at=time.time())
        return _SHOP["id"]


def _money(v):
    """Etsy money object -> Decimal dollars. USD only; anything else is unavailable."""
    try:
        amount, divisor, currency = v['amount'], v['divisor'], v['currency_code']
    except Exception:
        raise Unavailable('Etsy amount missing') from None
    if currency != 'USD':
        raise Unavailable('Etsy order in non-USD currency')
    try:
        value = Decimal(int(amount)) / Decimal(int(divisor))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        raise Unavailable('Etsy amount invalid') from None
    if not value.is_finite() or value < 0:
        raise Unavailable('Etsy amount invalid')
    return value


def _normalize(r):
    """One receipt -> internal order, or None when it is not a countable sale."""
    try:
        created = datetime.fromtimestamp(int(r['created_timestamp']), timezone.utc)
    except Exception:
        return None
    if not r.get('is_paid'):
        return None
    status = str(r.get('status') or '').lower()
    if 'cancel' in status:
        return None
    gross = _money(r.get('grandtotal') or r.get('total_price'))
    tax = _money(r.get('total_tax_cost')) if r.get('total_tax_cost') is not None else Decimal('0')
    items = []
    for t in r.get('transactions') or []:
        try:
            q = int(t.get('quantity'))
        except Exception:
            q = 0
        if q <= 0:
            continue
        items.append({'title': (t.get('title') or '?').strip(), 'sku': (t.get('sku') or '').strip(), 'quantity': q})
    return {'receipt_id': int(r['receipt_id']), 'created': created, 'status': status or 'paid',
            'gross': gross, 'tax': tax, 'revenue': gross - tax, 'items': items}


def _receipts(params):
    """All receipts matching params, paged. Over-large or malformed sets are unavailable."""
    shop = _shop_id()
    out, offset = [], 0
    for _ in range(MAX_PAGES):
        data = _get(f'/v3/application/shops/{shop}/receipts',
                    dict(params, limit=100, offset=offset))
        results = data.get('results')
        if not isinstance(results, list):
            raise Unavailable('Etsy receipts unavailable')
        out.extend(results)
        if len(results) < 100:
            return out
        offset += 100
    raise Unavailable('Etsy period too large')


def cohort(start, end):
    """Countable receipts created in the half-open aware window [start, end)."""
    if start.tzinfo is None or (end is not None and end.tzinfo is None):
        raise Unavailable('Etsy window invalid')
    params = {'min_created': int(start.timestamp()), 'was_paid': 'true', 'sort_on': 'created', 'sort_order': 'asc'}
    if end is not None:
        params['max_created'] = int(end.timestamp())
    out = []
    for r in _receipts(params):
        n = _normalize(r)
        if n is not None and n['created'] >= start and (end is None or n['created'] < end):
            out.append(n)
    return out


def modified_since(epoch):
    """Receipts touched since epoch (5-minute overlap handled by the caller's state)."""
    return [n for n in (_normalize(r) for r in _receipts({
        'min_last_modified': int(epoch), 'sort_on': 'updated', 'sort_order': 'asc'})) if n is not None]


def _book_units(receipts):
    """Book units via the same costs.json mapping the website line uses, then the title heuristic."""
    cfg = costs.load_config()
    total = 0
    for r in receipts:
        for t in r['items']:
            li = {'title': t['title'], 'name': t['title'], 'sku': t['sku'], 'quantity': t['quantity'],
                  'product': {'title': t['title'], 'productType': '', 'tags': []}}
            try:
                it = costs.classify(li, cfg)
                if it:
                    n = costs.books_in(li, it)
                else:
                    name = t['title'].lower()
                    n = 1 if 'book' in name and 'coloring' not in name else 0
                total += n * t['quantity']
            except Exception as e:
                print('etsy books line skipped a line: ' + type(e).__name__)
    return total


def _product_units(receipts):
    """Individual product -> units, same costs.json mapping as the website line: known bundles
    expand into component books; coloring books and other non-books count as themselves."""
    cfg = costs.load_config()
    out = {}
    for r in receipts:
        for t in r['items']:
            li = {'title': t['title'], 'name': t['title'], 'sku': t['sku'], 'quantity': t['quantity'],
                  'product': {'title': t['title'], 'productType': '', 'tags': []}}
            try:
                for lab in costs.product_labels(li, cfg): out[lab] = out.get(lab, 0) + t['quantity']
            except Exception as e:
                print('etsy units line skipped a line: ' + type(e).__name__)
    return out


def summary(start, end):
    """{'orders', 'revenue' (Decimal, excl. tax, before Etsy fees), 'books', 'units'} for the window."""
    receipts = cohort(start, end)
    return {'orders': len(receipts), 'revenue': sum((r['revenue'] for r in receipts), Decimal('0')),
            'books': _book_units(receipts), 'units': _product_units(receipts)}


def sale_message(r):
    """Etsy-labeled sale alert, matching the marketplace alert shape."""
    when = r['created'].astimezone(ZoneInfo(c.TZ)).strftime('%b %-d %-I:%M %p ET')
    lines = [f"\U0001f6d2 ETSY SALE - ${r['revenue']:.2f} (excl. tax, before Etsy fees)", "",
             f"\U0001f9fe Etsy receipt {r['receipt_id']} · paid",
             f"\U0001f552 sold {when}", ""]
    if r['items']:
        lines.append("\U0001f4e6 " + ", ".join(f"{t['title']} x{t['quantity']}" for t in r['items']))
    return "\n".join(lines)
