import http.client
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class HealthTests(unittest.TestCase):
    def test_audio_setup_requires_local_origin_and_audio_preparation_requires_auth(self):
        local=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        worker=threading.Thread(target=local.serve_forever,daemon=True);worker.start()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',local.server_port,timeout=3)
            connection.request('POST','/api/audio-share/start',body='{}',headers={'Content-Type':'application/json'})
            response=connection.getresponse();response.read();self.assertEqual(response.status,403)
            connection.close()
            with patch.object(server.AUDIO_SHARE,'configure') as configure:
                for extra in [{'Host':'public.trycloudflare.com'},{'X-Forwarded-For':'203.0.113.2'},{'Origin':'https://another-site.example'}]:
                    connection=http.client.HTTPConnection('127.0.0.1',local.server_port,timeout=3)
                    connection.request('POST','/api/audio-share/config',body='{"token":"not-a-real-token"}',headers={'Content-Type':'application/json','Cookie':'musiclab_access='+server.KEY,**extra})
                    response=connection.getresponse();response.read();self.assertEqual(response.status,403)
                    connection.close()
                configure.assert_not_called()
            connection=http.client.HTTPConnection('127.0.0.1',local.server_port,timeout=3)
            connection.request('GET','/api/shared-audio/invalid.mp3')
            response=connection.getresponse();response.read();self.assertEqual(response.status,410);connection.close()
        finally:local.shutdown();local.server_close();worker.join()

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
