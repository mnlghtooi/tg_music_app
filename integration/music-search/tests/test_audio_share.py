import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import shutil
import subprocess
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from audio_share import AudioShare, telegram_user, MAX_BYTES
from entity_service import EntityService
from bandcamp_service import BandcampService, identifier


TOKEN = '123456:fake-local-test-token-not-a-real-bot'


def signed(user_id=123, age=0, **extra):
    data = dict(auth_date=str(int(time.time())-age),user=json.dumps({'id':user_id}),**extra)
    secret = hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
    digest = hmac.new(secret,'\n'.join(k+'='+v for k,v in sorted(data.items())).encode(),hashlib.sha256).hexdigest()
    return urlencode(dict(data,hash=digest))


class AudioShareTests(unittest.TestCase):
    def test_preparation_rejects_a_short_output_instead_of_sending_it_as_full_track(self):
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);fixture=root/'short.mp3'
            import os
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=2','-y',str(fixture)],
                check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            service=AudioShare(root,Mock());service.slots.acquire()
            service.jobs['job']=dict(source='bandcamp',item_id='track',created=time.time(),state='working')
            service.descriptor=lambda *args:dict(title='Song',artist='Artist',duration=180,permalink='https://artist.bandcamp.com/track/song')
            service.download_bandcamp=lambda item,target,started:shutil.copyfile(fixture,target)
            service.prepare('job')
            self.assertEqual(service.status('job')['state'],'error')
            self.assertIn('полный трек',service.status('job')['error'])
            self.assertFalse((root/'.cache/audio-share/job.mp3').exists())
            service.pool.shutdown()

    def test_telegram_signature_expiry_duplicate_keys_and_wrong_bot(self):
        self.assertEqual(telegram_user(signed(),TOKEN),123)
        for data in [signed(age=8000),signed(user_id=-1),signed()+'&user=forged',signed().replace('123','999')]:
            with self.assertRaises(ValueError): telegram_user(data,TOKEN)
        with self.assertRaises(ValueError): telegram_user(signed(),'other-bot')

    def test_protected_tracks_are_rejected_before_downloading(self):
        entities = Mock()
        entities.sc.get_track.return_value={'policy':'SNIP'}
        with tempfile.TemporaryDirectory() as root:
            service=AudioShare(root,entities)
            with self.assertRaisesRegex(ValueError,'полный трек'):service.descriptor('soundcloud','12')
            with self.assertRaises(ValueError):service.descriptor('soundcloud','../12')
            service.pool.shutdown()

    def test_bandcamp_download_is_bounded_and_redirect_hosts_are_checked(self):
        with tempfile.TemporaryDirectory() as root:
            entities=Mock();entities.bc.audio_url.return_value='https://t4.bcbits.com/audio'
            service=AudioShare(root,entities)
            service.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1/private'})))
            with self.assertRaises(ValueError):service.download_bandcamp('id',Path(root)/'file',time.time())
            service.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-length':str(MAX_BYTES+1)})))
            with self.assertRaisesRegex(ValueError,'большое'):service.download_bandcamp('id',Path(root)/'file',time.time())
            with self.assertRaises(ValueError):service.limit_download({'downloaded_bytes':MAX_BYTES+1},time.time())
            service.pool.shutdown()

    def test_prepared_share_is_bound_to_signed_user_and_does_not_send_message(self):
        with tempfile.TemporaryDirectory() as root:
            service=AudioShare(root,Mock());file=Path(root)/'song.mp3';file.write_bytes(b'audio')
            service.jobs['job']=dict(state='ready',created=time.time(),path=file,title='Song',artist='Artist',filename='Song.mp3')
            service.token=lambda:TOKEN
            service.bot_call=Mock(return_value={'id':'prepared-id'})
            result=service.prepared_message('job',signed(),'https://local-test.trycloudflare.com')
            self.assertEqual(result['id'],'prepared-id')
            args=service.bot_call.call_args
            self.assertEqual(args.args[0],'savePreparedInlineMessage')
            self.assertEqual(args.kwargs['user_id'],123)
            self.assertEqual(args.kwargs['result']['type'],'audio')
            capability=args.kwargs['result']['audio_url'].rsplit('/',1)[-1].removesuffix('.mp3')
            self.assertEqual(service.public_file(capability)['path'],file)
            self.assertNotIn(TOKEN,str(result))
            with self.assertRaises(ValueError):service.prepared_message('job',signed(),'http://localhost')
            service.public[capability]=('job',time.time()-1)
            with self.assertRaises(ValueError):service.public_file(capability)
            service.pool.shutdown()

    def test_bot_setup_checks_username_and_keeps_token_out_of_response(self):
        with tempfile.TemporaryDirectory() as root:
            service=AudioShare(root,Mock());service.bot_call=Mock(return_value={'username':'someone_else_bot'})
            with self.assertRaises(ValueError):service.configure(TOKEN)
            self.assertFalse((Path(root)/'.private/telegram.json').exists())
            service.bot_call.return_value={'username':'mnlgth_music_app_bot'}
            result=service.configure(TOKEN)
            self.assertTrue(result['configured']);self.assertNotIn(TOKEN,str(result))
            self.assertEqual(service.token(),TOKEN)
            service.pool.shutdown()


if __name__=='__main__':unittest.main()
