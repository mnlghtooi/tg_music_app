import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import launch


class LaunchRestartTests(unittest.TestCase):
    def test_user_restart_only_targets_verified_server_pid(self):
        with patch.object(launch.os,'name','nt'), patch.object(launch,'saved_state',return_value={'pid':12345}), \
             patch.object(launch.urllib.request,'urlopen') as opened, \
             patch.object(launch.subprocess,'check_output',return_value='TCP 127.0.0.1:8765 0.0.0.0:0 LISTENING 12345'), \
             patch.object(launch.subprocess,'run') as stop, \
             patch.object(launch.socket,'create_connection',side_effect=OSError()):
            opened.return_value.__enter__.return_value=io.BytesIO(b'{"ok":true,"provider":"soundcloud"}')
            launch.restart_previous()
            self.assertEqual(stop.call_args.args[0],['taskkill','/PID','12345','/F'])
            self.assertNotIn('/T',stop.call_args.args[0])

    def test_mismatched_port_owner_is_never_stopped(self):
        with patch.object(launch.os,'name','nt'), patch.object(launch,'saved_state',return_value={'pid':12345}), \
             patch.object(launch.urllib.request,'urlopen') as opened, \
             patch.object(launch.subprocess,'check_output',return_value='TCP 127.0.0.1:8765 0.0.0.0:0 LISTENING 98765'), \
             patch.object(launch.subprocess,'run') as stop:
            opened.return_value.__enter__.return_value=io.BytesIO(b'{"ok":true,"provider":"soundcloud"}')
            launch.restart_previous()
            stop.assert_not_called()

    def test_missing_or_unauthorized_server_is_never_stopped(self):
        with patch.object(launch.os,'name','nt'), patch.object(launch,'saved_state',return_value={'pid':12345}), \
             patch.object(launch.urllib.request,'urlopen',side_effect=OSError()), \
             patch.object(launch.subprocess,'run') as stop:
            launch.restart_previous()
            stop.assert_not_called()

    def test_tunnel_health_url_is_public_and_keyless(self):
        with patch.object(launch.urllib.request,'urlopen') as opened:
            response=opened.return_value.__enter__.return_value
            response.status=200
            response.read.return_value=b'{"ok":true,"version":"0.7.10"}'
            self.assertTrue(launch.probe_public_tunnel('example-abc.trycloudflare.com'))
            request=opened.call_args.args[0]
            self.assertEqual(request.full_url,'https://example-abc.trycloudflare.com/healthz')
            self.assertNotIn('/open/',request.full_url)
        with self.assertRaises(ValueError):
            launch.probe_public_tunnel('example.com')

    def test_phone_url_is_assembled_and_published_after_delayed_dns_success(self):
        out=io.StringIO()
        with patch.object(launch,'probe_public_tunnel',side_effect=[OSError('DNS'),False,True]) as verify, \
             redirect_stdout(out):
            self.assertTrue(launch.announce_public_tunnel(
                'example-abc.trycloudflare.com','private-key',pause=0,progress_every=2))
        self.assertEqual(verify.call_count,3)
        output=out.getvalue()
        self.assertIn('PHONE_URL_PENDING=https://example-abc.trycloudflare.com/open/private-key',output)
        self.assertIn('PHONE_URL=https://example-abc.trycloudflare.com/open/private-key',output)
        self.assertIn('Public HTTPS health check passed.',output)
        self.assertLess(output.index('PHONE_URL_PENDING='),output.index('PHONE_URL='))

    def test_dns_failure_keeps_pending_link_but_no_false_ready_confirmation(self):
        out=io.StringIO()
        stop=type('FakeStop',(),{})()
        stop.is_set=lambda:False
        waits=iter([False,True])
        stop.wait=lambda pause:next(waits)
        with patch.object(launch,'probe_public_tunnel',side_effect=OSError('DNS')), \
             redirect_stdout(out):
            self.assertFalse(launch.announce_public_tunnel(
                'example-abc.trycloudflare.com','private-key',stop_event=stop,pause=0,progress_every=2))
        self.assertIn('PHONE_URL_PENDING=',out.getvalue())
        self.assertNotIn('PHONE_URL=',out.getvalue())
        self.assertIn('retrying automatically',out.getvalue())

    def test_exit_stops_public_polling(self):
        out=io.StringIO()
        with patch.object(launch,'probe_public_tunnel') as verify, \
             redirect_stdout(out):
            stopped=launch.threading.Event()
            stopped.set()
            self.assertFalse(launch.announce_public_tunnel(
                'example-abc.trycloudflare.com','private-key',stop_event=stopped,pause=0))
            verify.assert_not_called()

