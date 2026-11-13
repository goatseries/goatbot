import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse, parse_qs
from bot import tiktok_connect as t

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t._store = self.tmp.name
        t._base = 'https://example.railway.app'
        t._ready = True
        t._meta = {'authorization_endpoint': 'https://business-api.tiktok.com/portal/mcp-tt4b-authorize', 'token_endpoint': 'https://business-api.tiktok.com/token'}
        t._save('client.json', {'client_id': 'public-client', 'redirect_uri': t._base + '/tiktok/callback'})
    def tearDown(self):
        self.tmp.cleanup()
    def auth(self):
        nonce=t.new_link().rsplit('/',1)[1]
        url,cookie=t.begin(nonce)
        return parse_qs(urlparse(url).query),cookie
    def test_pkce_and_single_use(self):
        nonce=t.new_link().rsplit('/',1)[1]
        url,_=t.begin(nonce)
        q=parse_qs(urlparse(url).query)
        self.assertEqual(q['code_challenge_method'], ['S256'])
        self.assertEqual(q['scope'], ['mcp:tt4b'])
        self.assertNotIn('verifier',url)
        with self.assertRaises(ValueError): t.begin(nonce)
    def test_bad_nonce(self):
        t.new_link()
        with self.assertRaises(ValueError): t.begin('wrong')
    def test_expired(self):
        nonce=t.new_link().rsplit('/',1)[1]
        p=t._load('pending.json');p['expires_at']=0;t._save('pending.json',p)
        with self.assertRaises(ValueError):t.begin(nonce)
    @patch.object(t.requests,'post')
    def test_state_and_cookie(self,post):
        q,cookie=self.auth()
        for args,browser in [({'state':['bad'],'code':['x']},cookie),({'state':q['state'],'code':['x']},'wrong')]:
            with self.assertRaises(ValueError):t.finish(args,browser)
        post.assert_not_called()
    @patch.object(t.requests,'post')
    def test_exchange_and_replay(self,post):
        q,cookie=self.auth()
        post.return_value=Mock(json=lambda:{'access_token':'test-secret','refresh_token':'test-refresh'})
        args={'state':q['state'],'code':['test-code']}
        t.finish(args,cookie)
        token=t._load('tokens.json')
        self.assertFalse(token['verified'])
        self.assertEqual(os.stat(os.path.join(t._store,'tokens.json')).st_mode&0o777,0o600)
        with self.assertRaises(ValueError): t.finish(args,cookie)
        self.assertEqual(post.call_count,1)
    @patch.object(t.requests,'post')
    def test_exchange_failure_single_use(self,post):
        q,cookie=self.auth();post.side_effect=RuntimeError('network')
        args={'state':q['state'],'code':['test-code']}
        with self.assertRaises(RuntimeError):t.finish(args,cookie)
        with self.assertRaises(ValueError):t.finish(args,cookie)
        self.assertEqual(post.call_count,1)
        self.assertIsNone(t._load('tokens.json'))
    def test_link_rotation(self):
        old=t.new_link().rsplit('/',1)[1];t.new_link()
        with self.assertRaises(ValueError):t.begin(old)
    def test_disabled_default(self):
        with patch.dict(os.environ,{},clear=True),patch.object(t,'BoundedServer') as server:
            t.start();server.assert_not_called()
    def test_invalid_origin_rejected(self):
        with self.assertRaises(ValueError):t.configure('http://localhost:1','/tmp')
    def test_no_volume_rejected(self):
        with self.assertRaises(ValueError):t.configure('https://example.com',self.tmp.name)


class CommandTests(unittest.TestCase):
    @patch('bot.telegram.requests.get')
    def test_only_owner_private_connection_existing_commands_intact(self, get):
        from bot import telegram
        with patch.dict(os.environ, {'TELEGRAM_CHAT_ID':'123','TELEGRAM_CHAT_IDS':'123,456','TELEGRAM_BOT_TOKEN':'test'}),patch.object(telegram.c,'DRY_RUN',False):
            messages = [
                {'chat':{'id':123,'type':'private'},'from':{'id':123},'text':'/tiktokconnect'},
                {'chat':{'id':456,'type':'private'},'from':{'id':456},'text':'/tiktokconnect'},
                {'chat':{'id':123,'type':'group'},'from':{'id':123},'text':'/tiktokconnect'},
                {'chat':{'id':123,'type':'private'},'from':{'id':999},'text':'/tiktokconnect'},
                {'chat':{'id':456,'type':'private'},'from':{'id':456},'text':'/t'},
                {'chat':{'id':456,'type':'private'},'from':{'id':456},'text':'/order 123'},
            ]
            get.return_value=Mock(status_code=200,json=lambda:{'result':[{'update_id':i,'message':m} for i,m in enumerate(messages)]})
            _,commands=telegram.commands(0)
            self.assertEqual(commands,[(123,'tiktokconnect'),(456,'t'),(456,'order 123')])

class StartupTests(unittest.TestCase):
    def test_invalid_port_does_not_break_bot(self):
        with patch.dict(os.environ,{'TIKTOK_CONNECT_ENABLED':'1','PORT':'bad'},clear=True):t.start()
    def test_listener_failure_does_not_break_bot(self):
        with patch.dict(os.environ,{'TIKTOK_CONNECT_ENABLED':'1'},clear=True),patch.object(t,'BoundedServer',side_effect=OSError('busy')):t.start()

class HTTPTests(unittest.TestCase):
    setUp = ConnectionTests.setUp
    tearDown = ConnectionTests.tearDown
    def test_http_preview_and_csrf(self):
        import threading
        server=t.ThreadingHTTPServer(('127.0.0.1',0),t.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            nonce=t.new_link().rsplit('/',1)[1]
            url='http://127.0.0.1:'+str(server.server_port)+'/tiktok/connect/'+nonce
            response=t.requests.get(url,timeout=3)
            self.assertEqual(response.status_code,200)
            self.assertFalse(t._load('pending.json')['used'])
            self.assertIn('Continue to TikTok',response.text)
            response=t.requests.post(url,headers={'Origin':'https://evil.invalid'},allow_redirects=False,timeout=3)
            self.assertEqual(response.status_code,403)
            self.assertFalse(t._load('pending.json')['used'])
            response=t.requests.post(url,headers={'Origin':t._base},allow_redirects=False,timeout=3)
            self.assertEqual(response.status_code,303)
            self.assertTrue(t._load('pending.json')['used'])
            self.assertIn('Secure',response.headers['Set-Cookie'])
            self.assertIn('HttpOnly',response.headers['Set-Cookie'])
            self.assertIn('no-store',response.headers['Cache-Control'])
        finally:server.shutdown();server.server_close()



class ReadinessTests(unittest.TestCase):
    setUp=ConnectionTests.setUp
    tearDown=ConnectionTests.tearDown
    def test_registration_failure_mints_no_link(self):
        meta={'registration_endpoint':'https://business-api.tiktok.com/register','authorization_endpoint':'https://business-api.tiktok.com/authorize','token_endpoint':'https://business-api.tiktok.com/token'}
        with patch.object(t.os.path,'ismount',return_value=True),patch.object(t.requests,'get',return_value=Mock(json=lambda:meta)),patch.object(t.requests,'post',side_effect=RuntimeError('registration failed')):
            with self.assertRaises(RuntimeError):t.configure('https://new.example',self.tmp.name)
        self.assertFalse(t._ready)
        self.assertIsNone(t._meta)
        with self.assertRaises(RuntimeError):t.new_link()
    def test_old_redirect_does_not_consume(self):
        nonce=t.new_link().rsplit('/',1)[1]
        t._save('client.json',{'client_id':'old','redirect_uri':'https://old.example/tiktok/callback'})
        with self.assertRaises(ValueError):t.begin(nonce)
        self.assertFalse(t._load('pending.json')['used'])
    def test_bind_failure_clears_readiness(self):
        with patch.dict(os.environ,{'TIKTOK_CONNECT_ENABLED':'1','TIKTOK_PUBLIC_ORIGIN':'https://example.com'},clear=True),patch.object(t,'BoundedServer',side_effect=OSError('busy')),patch.object(t,'configure') as configure:
            t.start()
            configure.assert_not_called()
        with self.assertRaises(RuntimeError):t.new_link()
    def test_socket_timeout_and_concurrency(self):
        import socket,threading
        server=t.BoundedServer(('127.0.0.1',0),t.Handler)
        connection=socket.create_connection(server.server_address)
        request,address=server.get_request()
        self.assertEqual(request.gettimeout(),5)
        request.close();connection.close()
        held=[server._slots.acquire(False) for _ in range(8)]
        self.assertTrue(all(held));self.assertFalse(server._slots.acquire(False))
        for _ in held:server._slots.release()
        server.server_close()

if __name__=='__main__':unittest.main()
