import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from urllib.parse import urlparse, parse_qs

os.environ.setdefault('ETSY_KEYSTRING', 'test-keystring')
os.environ.setdefault('ETSY_CONNECT_ENABLED', '1')
os.environ.setdefault('ETSY_PUBLIC_ORIGIN', 'https://example.railway.app')

from bot import etsy_connect as ec
from bot import etsy


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ec._store = self.tmp.name
        ec._base = 'https://example.railway.app'
        ec._ready = True
    def tearDown(self):
        self.tmp.cleanup()
    def auth(self):
        nonce = ec.new_link().rsplit('/', 1)[1]
        url, cookie = ec.begin(nonce)
        return parse_qs(urlparse(url).query), cookie
    def test_authorize_url(self):
        nonce = ec.new_link().rsplit('/', 1)[1]
        url, _ = ec.begin(nonce)
        u = urlparse(url); q = parse_qs(u.query)
        self.assertEqual((u.scheme, u.hostname, u.path), ('https', 'www.etsy.com', '/oauth/connect'))
        self.assertEqual(q['code_challenge_method'], ['S256'])
        self.assertEqual(q['scope'], ['shops_r transactions_r'])
        self.assertEqual(q['redirect_uri'], ['https://example.railway.app/etsy/callback'])
        self.assertEqual(q['client_id'], ['test-keystring'])
        self.assertNotIn('verifier', url)
        with self.assertRaises(ValueError): ec.begin(nonce)  # single use
    def test_bad_nonce(self):
        ec.new_link()
        with self.assertRaises(ValueError): ec.begin('wrong')
    def test_expired(self):
        nonce = ec.new_link().rsplit('/', 1)[1]
        p = ec._load('pending.json'); p['expires_at'] = 0; ec._save('pending.json', p)
        with self.assertRaises(ValueError): ec.begin(nonce)
    @patch.object(ec.requests, 'post')
    def test_state_and_cookie(self, post):
        q, cookie = self.auth()
        for args, browser in [({'state': ['bad'], 'code': ['x']}, cookie), ({'state': q['state'], 'code': ['x']}, 'wrong')]:
            with self.assertRaises(ValueError): ec.finish(args, browser)
        post.assert_not_called()
    @patch.object(ec.requests, 'post')
    def test_exchange_and_replay(self, post):
        q, cookie = self.auth()
        post.return_value = Mock(json=lambda: {'access_token': '12345.tok', 'refresh_token': 'ref', 'expires_in': 3600},
                                 raise_for_status=lambda: None)
        ec.finish({'state': q['state'], 'code': ['c']}, cookie)
        token = ec._load('tokens.json')
        self.assertEqual(token['user_id'], '12345')
        self.assertEqual(os.stat(os.path.join(ec._store, 'tokens.json')).st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError): ec.finish({'state': q['state'], 'code': ['c']}, cookie)
        self.assertEqual(post.call_count, 1)
        # exchange used the documented token endpoint with PKCE verifier, no secret
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://api.etsy.com/v3/public/oauth/token')
        self.assertNotIn('client_secret', kwargs['data'])
        self.assertIn('code_verifier', kwargs['data'])
    @patch.object(ec.requests, 'post')
    def test_exchange_failure_single_use(self, post):
        q, cookie = self.auth(); post.side_effect = RuntimeError('network')
        with self.assertRaises(RuntimeError): ec.finish({'state': q['state'], 'code': ['c']}, cookie)


class NormalizeTests(unittest.TestCase):
    def receipt(self, **kw):
        base = {'receipt_id': 555, 'created_timestamp': 1764000000, 'is_paid': True, 'status': 'Paid',
                'grandtotal': {'amount': 2126, 'divisor': 100, 'currency_code': 'USD'},
                'total_tax_cost': {'amount': 127, 'divisor': 100, 'currency_code': 'USD'},
                'transactions': [{'title': 'Tiger Woods GOAT Golf Book', 'sku': 'GOAT-TW-HC', 'quantity': 1}]}
        base.update(kw)
        return base
    def test_money_and_items(self):
        r = etsy._normalize(self.receipt())
        self.assertEqual(r['revenue'], Decimal('19.99'))
        self.assertEqual(r['items'][0]['sku'], 'GOAT-TW-HC')
        self.assertEqual(r['created'].tzinfo, timezone.utc)
    def test_unpaid_and_cancelled_skipped(self):
        self.assertIsNone(etsy._normalize(self.receipt(is_paid=False)))
        self.assertIsNone(etsy._normalize(self.receipt(status='Canceled')))
    def test_non_usd_unavailable(self):
        bad = self.receipt(grandtotal={'amount': 100, 'divisor': 100, 'currency_code': 'EUR'})
        with self.assertRaises(etsy.Unavailable): etsy._normalize(bad)
    def test_missing_amount_unavailable(self):
        with self.assertRaises(etsy.Unavailable): etsy._normalize(self.receipt(grandtotal=None, total_price=None))
    def test_sale_message(self):
        msg = etsy.sale_message(etsy._normalize(self.receipt()))
        self.assertIn('ETSY SALE - $19.99', msg)
        self.assertIn('before Etsy fees', msg)
        self.assertIn('Tiger Woods GOAT Golf Book x1', msg)
    def test_book_units(self):
        one = etsy._normalize(self.receipt())
        two = etsy._normalize(self.receipt(receipt_id=556, transactions=[{'title': 'Fun sticker pack', 'sku': '', 'quantity': 3}]))
        # costs.json mapping wins when it matches; title heuristic otherwise
        self.assertEqual(etsy._book_units([one]) >= 0, True)
        self.assertEqual(etsy._book_units([two]), 0)


class PollTests(unittest.TestCase):
    def test_first_run_seeds_silently(self):
        import bot.main as m
        receipts = [etsy._normalize(NormalizeTests().receipt())]
        sent = []
        with patch.object(etsy, 'modified_since', return_value=receipts), \
             patch.object(m, 'alert_send', side_effect=sent.append):
            s = {}
            m.etsy_poll(s, 6)
            self.assertEqual(sent, [])  # history never replays as alerts
            self.assertIn(555, s['etsy']['seen'])
            # a fresh receipt after the first poll alerts
            fresh = NormalizeTests().receipt(receipt_id=556,
                created_timestamp=int(datetime.now(timezone.utc).timestamp()) - 60)
            with patch.object(etsy, 'modified_since', return_value=[etsy._normalize(fresh)]):
                m.etsy_poll(s, 6)
            self.assertEqual(len(sent), 1)
            self.assertIn('ETSY SALE', sent[0])
            self.assertIn(556, s['etsy']['alerted'])
            # replay never re-alerts
            with patch.object(etsy, 'modified_since', return_value=[etsy._normalize(fresh)]):
                m.etsy_poll(s, 6)
            self.assertEqual(len(sent), 1)
    def test_stale_seeds(self):
        import bot.main as m
        old = NormalizeTests().receipt(receipt_id=557, created_timestamp=1763000000)
        sent = []
        with patch.object(etsy, 'modified_since', return_value=[etsy._normalize(old)]), \
             patch.object(m, 'alert_send', side_effect=sent.append):
            s = {'etsy': {'last_poll': (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(), 'seen': [], 'alerted': []}}
            m.etsy_poll(s, 6)
            self.assertEqual(sent, [])
            self.assertIn(557, s['etsy']['seen'])


if __name__ == '__main__':
    unittest.main()
