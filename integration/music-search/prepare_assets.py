"""Fetch the pinned player assets for a checkout or hosting build."""
import hashlib
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
HLS_SHA256 = '7c47cd97d7a6e7b98d9623dd8ed9a6d45af4be4085e0c2001cd7175c2b4cfb07'


def prepare_assets():
    target = ROOT / 'hls.min.js'
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != HLS_SHA256:
        with urlopen('https://cdn.jsdelivr.net/npm/hls.js@1.6.13/dist/hls.min.js', timeout=45) as response:
            content = response.read(2 * 1024 * 1024)
        if hashlib.sha256(content).hexdigest() != HLS_SHA256:
            raise RuntimeError('HLS player checksum mismatch')
        target.write_bytes(content)
    license_file = ROOT / 'HLS-LICENSE.txt'
    if not license_file.exists():
        with urlopen('https://raw.githubusercontent.com/video-dev/hls.js/v1.6.13/LICENSE', timeout=30) as response:
            license_file.write_bytes(response.read(128 * 1024))


if __name__ == '__main__':
    prepare_assets()
    print('Pinned HLS assets ready.')
