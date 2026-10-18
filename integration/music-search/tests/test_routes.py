import http.client
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def request(self, path, cookie=None):
        client = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
        client.request('GET', path, headers={'Cookie': cookie} if cookie else {})
        response = client.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        client.close()
        return result

    def test_current_link_and_html(self):
        status, headers, _ = self.request('/open/' + server.KEY)
        self.assertEqual(status, 302)
        cookie = headers['Set-Cookie'].split(';')[0]
        for path in ['/', '/index.html']:
            status, headers, body = self.request(path, cookie)
            self.assertEqual(status, 200)
            self.assertIn(b'<!doctype html>', body)

    def test_expired_link_with_existing_cookie(self):
        cookie = 'musiclab_access=' + server.KEY
        for auth in [None, cookie]:
            status, headers, body = self.request('/open/expired-key', auth)
            self.assertEqual(status, 403)
            self.assertIn('text/html', headers['Content-Type'])
            self.assertIn(b'START.cmd', body)
            self.assertNotIn(server.KEY.encode(), body)

    def test_api_still_requires_authentication(self):
        self.assertEqual(self.request('/api/search?q=test')[0], 403)
