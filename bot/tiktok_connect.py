"""TikTok connection bootstrap only. Reporting changes are a separate rollout.

No token is served publicly or logged. Only the home bot chat may mint a 15-minute
connection link. Every authorization uses PKCE, single-use state and a browser
cookie bound to that state. No campaign/payment endpoints are called.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlparse, parse_qs
import requests

SERVER = 'https://business-api.tiktok.com/open_mcp/tt-ads-mcp-layer'
DISCOVERY = SERVER + '/oauth/.well-known/openid-configuration'
EXPECTED_ADVERTISER = '7538425138728042513'
LOCK = threading.RLock()
_store = None
_http = None
_meta = None
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

def configure(base, directory):
    global _store, _meta, _base, _ready
    _ready = False
    _meta = None
    parsed = urlparse(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('HTTPS public origin required')
    if not os.path.isdir(directory) or not os.path.ismount(directory):
        raise ValueError('Mounted persistent directory required')
    _base = base.rstrip('/')
    _store = os.path.join(directory, 'tiktok')
    os.makedirs(_store, mode=0o700, exist_ok=True)
    os.chmod(_store, 0o700)
    response = requests.get(DISCOVERY, timeout=30)
    response.raise_for_status()
    metadata = response.json()
    for key in ('registration_endpoint', 'authorization_endpoint', 'token_endpoint'):
        p = urlparse(metadata.get(key, ''))
        if p.scheme != 'https' or p.hostname != 'business-api.tiktok.com':
            raise ValueError('Unexpected TikTok authorization endpoint')
    redirect = _base + '/tiktok/callback'
    with LOCK:
        client = _load('client.json')
        if not client or client.get('redirect_uri') != redirect:
            response = requests.post(metadata['registration_endpoint'], json={
                'client_name': 'GOAT Series goatbot reporting', 'redirect_uris': [redirect],
                'token_endpoint_auth_method': 'none',
                'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
            }, timeout=30)
            response.raise_for_status()
            client = response.json()
            if not client.get('client_id'):
                raise ValueError('TikTok client registration failed')
            _save('client.json', {'client_id': client['client_id'], 'redirect_uri': redirect})
        if not client.get('client_id'):
            raise ValueError('Missing registered client')
        _meta = metadata

def new_link():
    if not _ready or not _meta:
        raise RuntimeError('TikTok connection endpoint is not configured yet')
    with LOCK:
        nonce = secrets.token_urlsafe(32)
        _save('pending.json', {'link_hash': hashlib.sha256(nonce.encode()).hexdigest(),
                              'expires_at': time.time() + 900, 'used': False})
    return _base + '/tiktok/connect/' + nonce

def begin(nonce):
    with LOCK:
        client = _load('client.json') or {}
        if not _ready or not _meta or not client.get('client_id') or client.get('redirect_uri') != _base + '/tiktok/callback':
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
        client = _load('client.json')
        return _meta['authorization_endpoint'] + '?' + urlencode({
            'response_type': 'code', 'resource': SERVER, 'client_id': client['client_id'],
            'redirect_uri': client['redirect_uri'], 'scope': 'mcp:tt4b',
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
        client = _load('client.json')
        pending['consumed'] = True
        _save('pending.json', pending)
        response = requests.post(_meta['token_endpoint'], data={
            'grant_type': 'authorization_code', 'client_id': client['client_id'],
            'code': args['code'][0], 'code_verifier': pending['verifier'],
            'redirect_uri': client['redirect_uri'],
        }, timeout=30)
        response.raise_for_status()
        token = response.json()
        if not token.get('access_token') or not token.get('refresh_token'):
            raise ValueError('Authorization token response incomplete')
        token.update({'created_at': time.time(), 'expected_advertiser': EXPECTED_ADVERTISER,
                      'client_id': client['client_id'], 'verified': False})
        _save('tokens.json', token)
        os.remove(os.path.join(_store, 'pending.json'))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Query strings contain authorization codes. Never log requests.

    def _headers(self, status=200, cookie=None, location=None, html=False):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'text/plain; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'self' https://business-api.tiktok.com; frame-ancestors 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        if location:
            self.send_header('Location', location)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if len(self.path) > 8192:
            self._headers(414)
            return
        try:
            if parsed.path == '/health':
                self._headers()
                self.wfile.write(b'OK')
            elif parsed.path.startswith('/tiktok/connect/'):
                nonce = parsed.path.removeprefix('/tiktok/connect/')
                if len(nonce) > 128 or '/' in nonce or parsed.query:
                    raise ValueError('Invalid link')
                # GET is safe for link previewers. Only an explicit button POST starts OAuth.
                if not nonce or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in nonce):
                    raise ValueError('Invalid link')
                self._headers(html=True)
                self.wfile.write(b'<!doctype html><meta name="viewport" content="width=device-width"><title>Connect TikTok to goatbot</title><h1>Connect TikTok to goatbot</h1><p>TikTok will show broad permissions. Goatbot will use account details and reporting only. No ads or payment settings will be changed.</p><p>TikTok may require periodic reauthorization. This setup does not enable reporting until the account and spend data are verified.</p><form method="post"><button type="submit">Continue to TikTok</button></form>')
            elif parsed.path == '/tiktok/callback':
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                browser = cookie.get('tiktok_oauth')
                finish(parse_qs(parsed.query), browser.value if browser else '')
                self._headers(303, 'tiktok_oauth=; Secure; HttpOnly; SameSite=Lax; Path=/tiktok; Max-Age=0', '/tiktok/connected')
            elif parsed.path == '/tiktok/connected':
                self._headers()
                self.wfile.write(b'TikTok authorization received. Account and spend verification is next. Reporting is not enabled yet. You can close this page.')
            else:
                self._headers(404)
                self.wfile.write(b'Not found')
        except Exception:
            self._headers(400)
            self.wfile.write(b'Connection could not complete. Return to goatbot and request a new connection link. No reporting change was enabled.')

    def do_POST(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith('/tiktok/connect/') or parsed.query or len(self.path) > 256:
            self._headers(405)
            return
        # A POST from another origin is not a user clicking our connection page.
        if self.headers.get('Origin') != _base:
            self._headers(403)
            return
        try:
            location, browser = begin(parsed.path.removeprefix('/tiktok/connect/'))
            self._headers(303, 'tiktok_oauth=' + browser + '; Secure; HttpOnly; SameSite=Lax; Path=/tiktok; Max-Age=900', location)
        except Exception:
            self._headers(400)
            self.wfile.write(b'Connection link expired or already used. Request a new link in goatbot.')


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        pass  # No query strings, request bytes or token errors in logs.


def start():
    """Bind first; discover/register off the reporting thread. Fail closed."""
    global _http, _ready, _meta
    _ready = False
    _meta = None
    if os.environ.get('TIKTOK_CONNECT_ENABLED') != '1':
        return
    try:
        port = int(os.environ.get('PORT', '8080'))
        _http = BoundedServer(('0.0.0.0', port), Handler)
        threading.Thread(target=_http.serve_forever, daemon=True).start()
    except Exception:
        _http = None
        print('TikTok listener failed; existing reporting continues')
        return
    base = os.environ.get('TIKTOK_PUBLIC_ORIGIN')
    if base:
        def setup():
            global _ready, _meta
            try:
                configure(base, os.environ.get('TIKTOK_DATA_DIR', '/data'))
                _ready = True
            except Exception:
                _ready = False
                _meta = None
                print('TikTok connection configuration failed; reporting remains disabled')
        threading.Thread(target=setup, daemon=True).start()
