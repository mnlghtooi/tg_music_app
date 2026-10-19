"""Run Music Lab locally and provide an optional temporary HTTPS link."""
import argparse, json, os, re, socket, subprocess, sys, threading, time, urllib.request, webbrowser
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'.private/local-server.json'

def saved_state():
    try:
        data=json.loads(STATE.read_text(encoding='utf-8'))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):return {}

previous=saved_state()
if isinstance(previous.get('key'),str) and re.fullmatch(r'[A-Za-z0-9_-]{16,128}',previous['key']):
    os.environ.setdefault('MUSICLAB_ACCESS_KEY',previous['key'])
import server

def restart_previous():
    """User-invoked restart: verify the private server and its listening PID first."""
    if os.name!='nt':return
    state=saved_state();pid=state.get('pid')
    if not isinstance(pid,int) or pid<=0 or pid==os.getpid():return
    try:
        request=urllib.request.Request('http://127.0.0.1:8765/api/health',
            headers={'Cookie':'musiclab_access='+server.KEY})
        with urllib.request.urlopen(request,timeout=2) as response:health=json.load(response)
        if health.get('ok') is not True or health.get('provider')!='soundcloud':return
        listing=subprocess.check_output(['netstat','-ano','-p','tcp'],text=True,errors='replace')
        owners=[]
        for line in listing.splitlines():
            fields=line.split()
            if len(fields)>=5 and fields[1]=='127.0.0.1:8765' and fields[3]=='LISTENING':owners.append(fields[-1])
        if str(pid) not in owners:return
    except (OSError,ValueError,subprocess.SubprocessError):return
    print('Restarting the existing Music Lab server...',flush=True)
    subprocess.run(['taskkill','/PID',str(pid),'/F'],check=True,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for attempt in range(30):
        try:
            with socket.create_connection(('127.0.0.1',8765),timeout=.2):pass
        except OSError:return
        time.sleep(.1)
    raise RuntimeError('The previous server is still listening on port 8765.')
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--local-only",action="store_true")
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument('--restart',action='store_true')
    args=parser.parse_args()
    from prepare_assets import prepare_assets
    prepare_assets()
    if args.restart:restart_previous()
    http=server.ThreadingHTTPServer(("127.0.0.1",8765),server.Handler)
    STATE.parent.mkdir(exist_ok=True)
    temporary=STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid':os.getpid(),'key':server.KEY}),encoding='utf-8')
    temporary.replace(STATE)
    threading.Thread(target=http.serve_forever,daemon=True).start()
    local="http://127.0.0.1:8765/open/"+server.KEY
    threading.Thread(target=server.warm_search,daemon=True).start()
    print("Music Lab 0.7.4 is running.",flush=True)
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

