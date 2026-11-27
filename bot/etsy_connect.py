"""Etsy connection bootstrap only. Reporting changes are a separate rollout.

No token is served publicly or logged. Only the home bot chat may mint a 15-minute
connection link. Every authorization uses PKCE, single-use state and a browser
cookie bound to that state. Read-only scopes only (shops_r, transactions_r).

Unlike TikTok, the Etsy app is registered manually in Etsy's developer portal;
the keystring and shared secret arrive as ETSY_KEYSTRING / ETSY_SHARED_SECRET.
The HTTP listener is owned by tiktok_connect, which dispatches /etsy/* here.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlparse, parse_qs
import requests

AUTHORIZE = 'https://www.etsy.com/oauth/connect'
TOKEN = 'https://api.etsy.com/v3/public/oauth/token'
API = 'https://api.etsy.com'
SCOPES = 'shops_r transactions_r'
LOCK = threading.RLock()
_store = None
_base = None
_ready = False


def _write(path, obj):
    temp = path + '.tmp'
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def _load(name, default=None):
    try:
        with open(os.path.join(_store, name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _save(name, data):
    _write(os.path.join(_store, name), data)


def _keystring():
    k = os.environ.get('ETSY_KEYSTRING', '').strip()
    if not k:
        raise RuntimeError('ETSY_KEYSTRING missing')
    return k


def api_key_header():
    """x-api-key for every Etsy call. Documented form is the bare keystring."""
    return _keystring()


def enabled():
    return os.environ.get('ETSY_CONNECT_ENABLED') == '1'


def ready():
    return _ready


def configure(base=None, directory=None):
    global _store, _base, _ready
    _ready = False
    base = base or os.environ.get('ETSY_PUBLIC_ORIGIN') or os.environ.get('TIKTOK_PUBLIC_ORIGIN') or ''
    directory = directory or os.environ.get('ETSY_DATA_DIR') or os.environ.get('TIKTOK_DATA_DIR', '/data')
    parsed = urlparse(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('HTTPS public origin required')
    if not os.path.isdir(directory) or not os.path.ismount(directory):
        raise ValueError('Mounted persistent directory required')
    for url, host in ((AUTHORIZE, 'www.etsy.com'), (TOKEN, 'api.etsy.com')):
        p = urlparse(url)
        if p.scheme != 'https' or p.hostname != host:
            raise ValueError('Unexpected Etsy endpoint')
    _keystring()  # fail fast when the app credentials were never set
    _base = base.rstrip('/')
    _store = os.path.join(directory, 'etsy')
    os.makedirs(_store, mode=0o700, exist_ok=True)
    os.chmod(_store, 0o700)
    _ready = True


def start():
    """Configure only; tiktok_connect owns the shared listener. Fail closed."""
    global _ready
    _ready = False
    if not enabled():
        return
    try:
        configure()
        _ready = True
    except Exception:
        _ready = False
        print('Etsy connection configuration failed; existing reporting continues')


def new_link():
    if not _ready:
        raise RuntimeError('Etsy connection endpoint is not configured yet')
    with LOCK:
        nonce = secrets.token_urlsafe(32)
        _save('pending.json', {'link_hash': hashlib.sha256(nonce.encode()).hexdigest(),
                               'expires_at': time.time() + 900, 'used': False})
    return _base + '/etsy/connect/' + nonce


def begin(nonce):
    with LOCK:
        if not _ready:
            raise ValueError('Connection unavailable')
        pending = _load('pending.json') or {}
        expected = pending.get('link_hash', '')
        if pending.get('used') or pending.get('expires_at', 0) < time.time() or not secrets.compare_digest(expected, hashlib.sha256(nonce.encode()).hexdigest()):
            raise ValueError('Connection link expired or already used')
        verifier = secrets.token_urlsafe(64)
        state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        pending.update({'used': True, 'state': state, 'verifier': verifier,
                        'browser_hash': hashlib.sha256(browser.encode()).hexdigest()})
        _save('pending.json', pending)
        return AUTHORIZE + '?' + urlencode({
            'response_type': 'code', 'client_id': _keystring(),
            'redirect_uri': _base + '/etsy/callback', 'scope': SCOPES,
            'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': state,
        }), browser


def finish(args, browser):
    with LOCK:
        pending = _load('pending.json') or {}
        if not pending.get('used') or pending.get('consumed') or pending.get('expires_at', 0) < time.time():
            raise ValueError('Authorization expired or already consumed')
        if args.get('state') != [pending.get('state')] or not browser or not secrets.compare_digest(pending.get('browser_hash', ''), hashlib.sha256(browser.encode()).hexdigest()):
            raise ValueError('Authorization state mismatch')
        if len(args.get('code', [])) != 1:
            raise ValueError('Authorization code missing')
        pending['consumed'] = True
        _save('pending.json', pending)
        response = requests.post(TOKEN, data={
            'grant_type': 'authorization_code', 'client_id': _keystring(),
            'redirect_uri': _base + '/etsy/callback',
            'code': args['code'][0], 'code_verifier': pending['verifier'],
        }, timeout=30)
        response.raise_for_status()
        token = response.json()
        if not token.get('access_token') or not token.get('refresh_token') or not token.get('expires_in'):
            raise ValueError('Authorization token response incomplete')
        try:
            user_id = str(token['access_token']).split('.')[0]
            if not user_id.isdigit():
                raise ValueError()
        except Exception:
            raise ValueError('Authorization token response incomplete') from None
        token.update({'created_at': time.time(), 'user_id': user_id})
        _save('tokens.json', token)
        os.remove(os.path.join(_store, 'pending.json'))


def handle_get(handler, parsed):
    """/etsy/* GET routes, dispatched from the shared listener in tiktok_connect."""
    if parsed.path.startswith('/etsy/connect/'):
        nonce = parsed.path.removeprefix('/etsy/connect/')
        if not nonce or len(nonce) > 128 or '/' in nonce or parsed.query:
            raise ValueError('Invalid link')
        # GET is safe for link previewers. Only an explicit button POST starts OAuth.
        if any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in nonce):
            raise ValueError('Invalid link')
        handler._headers(html=True)
        handler.wfile.write(b'<!doctype html><meta name="viewport" content="width=device-width"><title>Connect Etsy to goatbot</title><h1>Connect Etsy to goatbot</h1><p>Goatbot will read your shop and order data only. Nothing will be listed, changed or sold.</p><p>Etsy may require periodic reauthorization. This setup does not enable reporting until order data is verified.</p><form method="post"><button type="submit">Continue to Etsy</button></form>')
    elif parsed.path == '/etsy/callback':
        cookie = SimpleCookie(handler.headers.get('Cookie', ''))
        browser = cookie.get('etsy_oauth')
        finish(parse_qs(parsed.query), browser.value if browser else '')
        handler._headers(303, 'etsy_oauth=; Secure; HttpOnly; SameSite=Lax; Path=/etsy; Max-Age=0', '/etsy/connected')
    elif parsed.path == '/etsy/connected':
        handler._headers()
        handler.wfile.write(b'Etsy authorization received. Order data verification is next. You can close this page.')
    else:
        handler._headers(404)
        handler.wfile.write(b'Not found')


def handle_post(handler, parsed):
    """/etsy/connect/<nonce> POST, dispatched from the shared listener."""
    if not parsed.path.startswith('/etsy/connect/') or parsed.query or len(handler.path) > 256:
        handler._headers(405)
        return
    # A POST from another origin is not a user clicking our connection page.
    if handler.headers.get('Origin') != _base:
        handler._headers(403)
        return
    try:
        location, browser = begin(parsed.path.removeprefix('/etsy/connect/'))
        handler._headers(303, 'etsy_oauth=' + browser + '; Secure; HttpOnly; SameSite=Lax; Path=/etsy; Max-Age=900', location)
    except Exception:
        handler._headers(400)
        handler.wfile.write(b'Connection link expired or already used. Request a new link in goatbot.')
