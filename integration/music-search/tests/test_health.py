import http.client
from pathlib import Path
import sys
import threading
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class HealthTests(unittest.TestCase):
    def test_health_is_public_but_music_api_is_private(self):
        local = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        worker = threading.Thread(target=local.serve_forever, daemon=True); worker.start()
        try:
            connection = http.client.HTTPConnection('127.0.0.1', local.server_port, timeout=3)
            connection.request('GET', '/healthz')
            response = connection.getresponse(); body = response.read()
            self.assertEqual(response.status, 200)
            self.assertNotIn(server.KEY.encode(), body)
            self.assertNotIn('Set-Cookie', response.headers)
            connection.request('GET', '/api/search?q=test')
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 403)
            connection.close()
        finally:
            local.shutdown(); local.server_close(); worker.join()
