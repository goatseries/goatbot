"""Account metadata + spend reads only, using owner-authorized TikTok OAuth.

UTC is selected in the report query. The response's metrics.timezone describes
account metadata even with UTC hour dimensions (verified by paired live reports).
"""
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import requests
from . import tiktok_connect as auth

SERVER = auth.SERVER
ADVERTISER = auth.EXPECTED_ADVERTISER
TOKEN_ENDPOINT = SERVER + '/oauth/token'
READ_TOOLS = frozenset({'advertiser_info_get', 'report_integrated_get'})
_CACHE = {}
_REPORT_CACHE = {}
_BUDGET = threading.local()
_ACCOUNT = None
_LOCK = threading.RLock()

class Unavailable(RuntimeError):
    pass

def _timeout():
    remaining = getattr(_BUDGET, 'deadline', time.monotonic() + 20) - time.monotonic()
    if remaining <= 0.1:
        raise Unavailable('TikTok reporting timed out; retry report')
    return (min(3, remaining / 2), min(8, remaining / 2))

def _expiry(token, absolute, relative):
    value = token[absolute] if absolute in token else float(token['created_at']) + float(token[relative])
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise Unavailable('TikTok authorization expiry invalid')
    return value

def _money(value):
    if value is None or isinstance(value, bool):
        raise Unavailable('TikTok spend missing')
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Unavailable('TikTok spend invalid') from None
    if not amount.is_finite() or amount < 0:
        raise Unavailable('TikTok spend invalid')
    return amount

def _token():
    # Shared with callback, so refresh never overwrites an in-process new grant.
    with auth.LOCK:
        path = os.path.join(os.environ.get('TIKTOK_DATA_DIR', '/data'), 'tiktok', 'tokens.json')
        try:
            with open(path) as f:
                token = json.load(f)
            refresh_deadline = _expiry(token, 'refresh_expires_at', 'refresh_token_expires_in')
            access_deadline = _expiry(token, 'expires_at', 'expires_in')
            if token.get('expected_advertiser') != ADVERTISER or token.get('scope') != 'mcp:tt4b':
                raise Unavailable('TikTok authorization mismatch')
            if time.time() >= refresh_deadline - 60:
                raise Unavailable('TikTok reauthorization required: /tiktokconnect in owner chat')
            if time.time() >= access_deadline - 300:
                response = requests.post(TOKEN_ENDPOINT, data={
                    'grant_type': 'refresh_token', 'refresh_token': token['refresh_token'],
                    'client_id': token['client_id'],
                }, timeout=_timeout())
                response.raise_for_status()
                updated = response.json()
                lifetime = float(updated.get('expires_in', 0))
                if not updated.get('access_token') or not math.isfinite(lifetime) or lifetime <= 0:
                    raise Unavailable('TikTok token refresh failed')
                token['access_token'] = updated['access_token']
                if updated.get('refresh_token'):
                    token['refresh_token'] = updated['refresh_token']
                token['expires_at'] = min(time.time() + lifetime, refresh_deadline)
                token['refresh_expires_at'] = refresh_deadline  # Never extend original grant.
                auth._write(path, token)
            return token['access_token']
        except Unavailable:
            raise
        except Exception:
            raise Unavailable('TikTok connection unavailable') from None

def _call(name, arguments):
    if name not in READ_TOOLS:
        raise ValueError('TikTok write methods are not permitted')
    try:
        response = requests.post(SERVER, headers={
            'Authorization': 'Bearer ' + _token(), 'Accept': 'application/json, text/event-stream',
        }, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                 'params': {'name': name, 'arguments': arguments}}, timeout=_timeout())
        response.raise_for_status()
        if 'text/event-stream' in response.headers.get('Content-Type', ''):
            frames = [json.loads(line[5:].strip()) for line in response.text.splitlines()
                      if line.startswith('data:') and line[5:].strip()]
            if len(frames) != 1:
                raise Unavailable('TikTok reporting response invalid')
            envelope = frames[0]
        else:
            envelope = response.json()
        result = envelope.get('result') or {}
        if envelope.get('error') or result.get('isError'):
            raise Unavailable('TikTok reporting returned an error')
        content = [x['text'] for x in result.get('content', []) if x.get('type') == 'text']
        if len(content) != 1:
            raise Unavailable('TikTok reporting response invalid')
        payload = json.loads(content[0])
        if payload.get('code') != 0 or not isinstance(payload.get('data'), dict):
            raise Unavailable('TikTok reporting data unavailable')
        return payload['data']
    except Unavailable:
        raise
    except Exception:
        raise Unavailable('TikTok reporting request failed') from None

def _account():
    global _ACCOUNT
    if _ACCOUNT and 0 <= time.time() - _ACCOUNT[0] < 300:
        return _ACCOUNT[1]
    data = _call('advertiser_info_get', {'advertiser_ids': [ADVERTISER],
                 'fields': ['advertiser_id', 'name', 'currency', 'timezone', 'display_timezone']})
    rows = data.get('list')
    if not isinstance(rows, list) or len(rows) != 1:
        raise Unavailable('TikTok account unavailable')
    account = rows[0]
    if str(account.get('advertiser_id')) != ADVERTISER or account.get('currency') != 'USD' or account.get('timezone') != 'Etc/GMT+9':
        raise Unavailable('TikTok account currency or timezone changed')
    _ACCOUNT = (time.time(), account)
    return account

def _report_rows(first, last):
    if first != last:
        raise Unavailable('TikTok hourly queries require a single date')
    key = (first, last)
    cached = _REPORT_CACHE.get(key)
    if cached and 0 <= time.time() - cached['cached_at'] < 300:
        return cached
    rows, expected_total = [], None
    for page in range(1, 101):
        data = _call('report_integrated_get', {
            'advertiser_ids': [ADVERTISER], 'report_type': 'BASIC', 'data_level': 'AUCTION_ADVERTISER',
            'dimensions': ['advertiser_id', 'stat_time_hour'], 'metrics': ['spend'],
            'start_date': first, 'end_date': last, 'multi_adv_report_in_utc_time': True,
            'page': page, 'page_size': 1000,
        })
        info = data.get('page_info') or {}
        chunk = data.get('list')
        total = info.get('total_number')
        pages = info.get('total_page')
        if not isinstance(chunk, list) or type(total) is not int or total < 0 or type(pages) is not int or pages < 0 or pages > 100 or type(info.get('page')) is not int or info.get('page') != page or (total > 0 and pages == 0):
            raise Unavailable('TikTok reporting coverage unavailable')
        if expected_total is not None and total != expected_total:
            raise Unavailable('TikTok pagination changed during report')
        expected_total = total
        rows.extend(chunk)
        if page >= max(1, pages):
            if len(rows) != total:
                raise Unavailable('TikTok reporting incomplete')
            snapshot = {'rows': rows, 'fetched_at': datetime.now(timezone.utc), 'cached_at': time.time()}
            if len(_REPORT_CACHE) > 100:
                _REPORT_CACHE.clear()
            _REPORT_CACHE[key] = snapshot
            return snapshot
    raise Unavailable('TikTok report too large')

def _hourly(first, last):
    return _report_rows(first, last)['rows']

def total_rows(rows, start, end):
    if not rows:
        raise Unavailable('TikTok returned no rows; zero spend not verified')
    amount, seen, matched = Decimal('0'), set(), 0
    for row in rows:
        dim, metrics = row.get('dimensions') or {}, row.get('metrics') or {}
        if str(dim.get('advertiser_id')) != ADVERTISER or str(metrics.get('advertiser_id', ADVERTISER)) != ADVERTISER or metrics.get('currency') != 'USD' or metrics.get('timezone') != 'Etc/GMT+9':
            raise Unavailable('TikTok reporting account/currency mismatch')
        try:
            hour = datetime.strptime(dim['stat_time_hour'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            raise Unavailable('TikTok report hour invalid') from None
        if hour.minute or hour.second or hour in seen:
            raise Unavailable('TikTok duplicate or invalid hour')
        seen.add(hour)
        value = _money(metrics.get('spend'))
        if start <= hour < end:
            amount += value
            matched += 1
    # Nonempty successful coverage may have rows outside the ET interval only.
    # Never infer that an entirely absent selected interval is zero spend.
    if not matched:
        raise Unavailable('TikTok returned no rows in this period; zero not verified')
    return amount

def spend(start, end, now=None):
    """Half-open aware period, UTC-aligned hours, including provider's current partial hour.

    Missing/empty/error responses never become zero. Five-minute successful snapshot
    cache reduces calls; a failed refresh never returns an older hidden fallback.
    """
    now = now or datetime.now(timezone.utc)
    if start.tzinfo is None or end.tzinfo is None or end <= start or end > now + timedelta(seconds=2):
        raise Unavailable('TikTok reporting interval invalid')
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if start.minute or start.second or start.microsecond:
        raise Unavailable('TikTok start must align with an hour')
    if end < now - timedelta(seconds=2) and (end.minute or end.second or end.microsecond):
        raise Unavailable('TikTok historic end must align with an hour')
    # Cache by calendar start + closed endpoint, or current report-hour for intraday.
    key = (start.isoformat(), end.replace(minute=0, second=0, microsecond=0).isoformat(), end == end.replace(minute=0,second=0,microsecond=0))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and 0 <= time.time() - cached['cached_at'] < 300:
            return dict(cached)
        _BUDGET.deadline = time.monotonic() + 20
        try:
            _account()
            rows, snapshots = [], []
            # Live hourly API accepts one date per request only. Dates are
            # padded to cover account-local and UTC filter interpretations;
            # returned hour dimensions are UTC under the explicit query flag.
            account_zone = timezone(-timedelta(hours=9))
            first = min(start.date(), start.astimezone(account_zone).date())
            last_hour = end - timedelta(microseconds=1)
            last = max(last_hour.date(), last_hour.astimezone(account_zone).date())
            while first <= last:
                snap = _report_rows(first.isoformat(), first.isoformat())
                snapshots.append(snap)
                rows.extend(snap['rows'])
                first += timedelta(days=1)
            amount = total_rows(rows, start, end)
            if not amount.is_finite() or amount > Decimal('1000000000000'):
                raise Unavailable('TikTok spend outside display range')
            snapshot = {'amount': amount, 'fetched_at': min(x['fetched_at'] for x in snapshots), 'cached_at': min(x['cached_at'] for x in snapshots)}
            if len(_CACHE) > 20:
                _CACHE.clear()
            _CACHE[key] = snapshot
            return dict(snapshot)
        finally:
            del _BUDGET.deadline
