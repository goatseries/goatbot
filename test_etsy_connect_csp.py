import unittest
from unittest.mock import patch, MagicMock
from bot import tiktok_connect

class ConnectPageCspTests(unittest.TestCase):
    def test_csp_allows_form_redirects_to_etsy_and_tiktok(self):
        """WebKit applies form-action to the redirect that follows the connect form's POST;
        without the OAuth host in form-action the browser blocks the redirect and the
        single-use link is consumed without anything happening (Ian, iPhone, Nov 27 2026)."""
        handler = MagicMock(spec=tiktok_connect.Handler)
        sent = {}
        handler.send_header = lambda k, v: sent.__setitem__(k, v)
        tiktok_connect.Handler._headers(handler)
        csp = sent.get('Content-Security-Policy', '')
        self.assertIn('https://www.etsy.com', csp)
        self.assertIn('https://business-api.tiktok.com', csp)
        self.assertIn("form-action 'self'", csp)

if __name__ == "__main__":
    unittest.main()
