import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
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
