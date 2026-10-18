"""Run Music Lab locally and provide an optional temporary HTTPS link."""
import argparse, os, re, subprocess, sys, threading, time, urllib.request, webbrowser
from pathlib import Path
import server

ROOT=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--local-only",action="store_true")
    parser.add_argument("--no-browser",action="store_true")
    args=parser.parse_args()
    if not (ROOT/"hls.min.js").exists():
        import hashlib
        data=urllib.request.urlopen("https://cdn.jsdelivr.net/npm/hls.js@1.6.13/dist/hls.min.js",timeout=45).read()
        if hashlib.sha256(data).hexdigest()!="7c47cd97d7a6e7b98d9623dd8ed9a6d45af4be4085e0c2001cd7175c2b4cfb07":
            raise RuntimeError("HLS player checksum mismatch")
        (ROOT/"hls.min.js").write_bytes(data)
        urllib.request.urlretrieve("https://raw.githubusercontent.com/video-dev/hls.js/v1.6.13/LICENSE",ROOT/"HLS-LICENSE.txt")
    http=server.ThreadingHTTPServer(("127.0.0.1",8765),server.Handler)
    threading.Thread(target=http.serve_forever,daemon=True).start()
    local="http://127.0.0.1:8765/open/"+server.KEY
    threading.Thread(target=server.warm_search,daemon=True).start()
    print("Music Lab 0.6.2 is running.",flush=True)
    print("Computer: "+local,flush=True)
    if not args.no_browser:webbrowser.open(local)
    tunnel=None
    try:
        if not args.local_only:
            tools=ROOT/".tools";tools.mkdir(exist_ok=True)
            exe=tools/"cloudflared.exe"
            if not exe.exists():
                print("Downloading Cloudflare HTTPS helper (first launch only)...",flush=True)
                temp=tools/"cloudflared.download"
                urllib.request.urlretrieve("https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",temp)
                temp.replace(exe)
            flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
            tunnel=subprocess.Popen([str(exe),"tunnel","--url","http://127.0.0.1:8765","--no-autoupdate","--protocol","http2"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",creationflags=flags)
            found=False
            for line in tunnel.stdout:
                match=re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",line)
                if match and not found:
                    found=True
                    public=match.group(0)+"/open/"+server.KEY
                    print("PHONE_URL="+public,flush=True)
                    print("Open this private HTTPS link on your iPhone. Keep this window open.",flush=True)
                if "ERR" in line:print("Tunnel: "+line.strip(),flush=True)
            if not found:print("HTTPS tunnel unavailable. Local browser still works.",flush=True)
        while True:time.sleep(1)
    except KeyboardInterrupt:pass
    except Exception as exc:
        print("HTTPS helper failed: "+type(exc).__name__+". Local browser still works.",flush=True)
        try:
            while True:time.sleep(1)
        except KeyboardInterrupt:pass
    finally:
        if tunnel:tunnel.terminate()
        http.shutdown()
if __name__=="__main__":main()

