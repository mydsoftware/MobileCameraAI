import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
import time
import webbrowser

import websocket
from flask import Flask, Response, jsonify, render_template_string, request

HOST = os.getenv("CAMERA_HOST", "37.202.152.217")
PORT = int(os.getenv("CAMERA_PORT", "8001"))
USERNAME = os.getenv("CAMERA_USERNAME", "admin")
PASSWORD = os.getenv("CAMERA_PASSWORD", "")
LOCAL_PORT = int(os.getenv("LOCAL_PORT", "5050"))

PRESETS = {
    "Camera 1 / Main": (8001, "/media/flv/video1"),
    "Camera 1 / Sub": (8001, "/media/flv/video2"),
    "Camera 1 / Third": (8001, "/media/flv/video3"),
    "Camera 2 / Main": (8002, "/media2/flv/video1"),
    "Camera 2 / Sub": (8002, "/media2/flv/video2"),
    "Camera 2 / Third": (8002, "/media2/flv/video3"),
    "Camera 3 / Main": (8003, "/media3/flv/video1"),
    "Camera 3 / Sub": (8003, "/media3/flv/video2"),
    "Camera 3 / Third": (8003, "/media3/flv/video3"),
}
DEFAULT_PORT = int(os.getenv("CAMERA_WS_PORT", str(PORT)))
DEFAULT_PATH = os.getenv("CAMERA_WS_PATH", "/media/flv/video2")

app = Flask(__name__)
state = {"connected": False, "authenticated": False, "packets": 0, "bytes": 0,
         "last_packet": "", "last_error": "", "challenge": "", "flv_bytes": 0,
         "player": "starting", "video_tags": 0, "keyframes": 0,
         "audio_tags": 0, "flv_header_ok": False, "stream_clients": 0,
         "ffmpeg": "starting", "decoded_frames": 0,
         "path": DEFAULT_PATH, "port": DEFAULT_PORT, "url": ""}

clients = []
clients_lock = threading.Lock()
ffmpeg_clients = []
ffmpeg_clients_lock = threading.Lock()
parser_lock = threading.Lock()
parser_buffer = bytearray()
stream_prefix = bytearray()
MAX_PREFIX = 4 * 1024 * 1024
ffmpeg_proc = None
stop_event = threading.Event()
stream_thread = None

PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>Uniview Multi Camera Live</title>
<style>
body{background:#111;color:#eee;font-family:Arial;margin:20px}h2{margin-bottom:10px}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.controls select,.controls input,.controls button{padding:9px;background:#222;color:#eee;border:1px solid #555;border-radius:5px}
img{width:min(100%,1280px);background:#000;display:block;min-height:240px;object-fit:contain}pre{background:#222;padding:12px;border-radius:6px;white-space:pre-wrap}
</style></head><body><h2>Uniview Multi-Camera Live</h2>
<div class="controls"><select id="preset"></select><input id="port" type="number" value="8001" min="1" max="65535"><input id="path" size="35" placeholder="/media/flv/video2"><button onclick="connect()">Connect</button></div>
<img id="m" src="/live.mjpg" alt="loading..."><pre id="s">connecting...</pre>
<script>
let presets={{ presets|tojson }};let p=document.getElementById('preset');let path=document.getElementById('path');let port=document.getElementById('port');
for(const [name,val] of Object.entries(presets)){let o=document.createElement('option');o.textContent=name;o.value=JSON.stringify(val);p.appendChild(o)}
p.value=JSON.stringify([{{ default_port }},{{ default_path|tojson }}]);let d=JSON.parse(p.value);port.value=d[0];path.value=d[1];p.onchange=()=>{let d=JSON.parse(p.value);port.value=d[0];path.value=d[1]};
async function connect(){let v=path.value.trim(),po=parseInt(port.value);if(!v.startsWith('/')){alert('Path must start with /');return}let r=await fetch('/switch?port='+po+'&path='+encodeURIComponent(v));let j=await r.json();if(!j.ok)alert(j.error);else document.getElementById('m').src='/live.mjpg?t='+Date.now()}
async function st(){try{let r=await fetch('/status');document.getElementById('s').textContent=JSON.stringify(await r.json(),null,2)}catch(e){}}setInterval(st,1000);st();
</script></body></html>'''


def md5(v):
    return hashlib.md5(v.encode()).hexdigest()


def make_url(port, path):
    return f"ws://{HOST}:{port}{path}"


def parse_challenge(text):
    m = re.search(r"realm=([^,\s]+).*?nonce=([^,\s]+).*?qop=([^,\s]+)", text)
    if not m:
        raise RuntimeError("Digest challenge not found")
    return m.group(1), m.group(2), m.group(3)


def make_digest(uri, realm, nonce, qop):
    nc = "00000001"
    cnonce = os.urandom(16).hex()
    ha1 = md5(f"{USERNAME}:{realm}:{PASSWORD}")
    ha2 = md5(f"GET:{uri}")
    response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return ('Digest ' + f'username="{USERNAME}", realm="{realm}", nonce="{nonce}", '
            f'algorithm="MD5", uri="{uri}", response="{response}", qop="{qop}", '
            f'nc="{nc}", cnonce="{cnonce}"')


def reset_state(port, path):
    global parser_buffer, stream_prefix
    with parser_lock:
        parser_buffer = bytearray()
        stream_prefix = bytearray()
    for k in ("packets", "bytes", "flv_bytes", "video_tags", "keyframes", "audio_tags", "decoded_frames"):
        state[k] = 0
    state.update({"connected": False, "authenticated": False, "last_packet":"", "last_error":"", "challenge":"",
                  "player":"starting", "flv_header_ok":False, "ffmpeg":"starting", "path":path, "port":port,
                  "url":make_url(port,path)})


def get_challenge(ws_url, port):
    # Camera 2's WebSocket implementation advertises/uses compression differently.
    # Disable permessage-deflate so websocket-client does not try to decode RSV1 frames.
    ws = websocket.create_connection(ws_url, origin=f"http://{HOST}:{port}", timeout=10, compression=None)
    state["connected"] = True
    msg = ws.recv(); ws.close()
    if isinstance(msg, bytes):
        raise RuntimeError("Binary data before authentication")
    state["challenge"] = msg
    data = json.loads(msg)
    if data.get("errorCode") != 401:
        raise RuntimeError(msg)
    return parse_challenge(data["detail"])


def open_authenticated(ws_url, port, auth):
    return websocket.create_connection(ws_url, origin=f"http://{HOST}:{port}", timeout=15, compression=None, header=[
        "Pragma: no-cache", "Cache-Control: no-cache",
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Cookie: langInfo_=1; noShowTip=1; Authorization=" + auth,
    ])


def broadcast(data):
    dead=[]
    with clients_lock:
        for c in clients:
            try:c.write(data)
            except Exception:dead.append(c)
        for c in dead:
            if c in clients:clients.remove(c)


def broadcast_ffmpeg(data):
    dead=[]
    with ffmpeg_clients_lock:
        for c in ffmpeg_clients:
            try:c.write(data)
            except Exception:dead.append(c)
        for c in dead:
            if c in ffmpeg_clients:ffmpeg_clients.remove(c)


def parse_flv_incremental(chunk):
    global parser_buffer
    with parser_lock:
        parser_buffer.extend(chunk)
        if len(parser_buffer)>MAX_PREFIX:del parser_buffer[:-MAX_PREFIX]
        data=parser_buffer
        if data.startswith(b"FLV") and len(data)>=13:
            state["flv_header_ok"]=True;pos=13
            while pos+15<=len(data):
                tag_type=data[pos];size=int.from_bytes(data[pos+1:pos+4],"big");end=pos+11+size+4
                if end>len(data):break
                if tag_type==9:
                    state["video_tags"]+=1
                    if size>=2:
                        v=data[pos+11:pos+11+size];codec=v[0]&15;frame=v[0]>>4
                        if codec==7 and len(v)>=2 and v[1]==1 and frame==1:state["keyframes"]+=1
                elif tag_type==8:state["audio_tags"]+=1
                pos=end
            if pos>13:del parser_buffer[:pos]


def ffmpeg_loop():
    global ffmpeg_proc
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg:
        state["ffmpeg"]="NOT_INSTALLED";state["last_error"]="FFmpeg not found in PATH";return
    try:
        ffmpeg_proc=subprocess.Popen([ffmpeg,"-hide_banner","-loglevel","warning","-fflags","+genpts","-probesize","20M","-analyzeduration","20M","-f","flv","-i","pipe:0","-an","-c:v","mjpeg","-q:v","5","-f","mpjpeg","-boundary_tag","frame","pipe:1"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        state["ffmpeg"]="running"
        def err():
            while ffmpeg_proc and ffmpeg_proc.poll() is None:
                line=ffmpeg_proc.stderr.readline()
                if line:state["last_error"]=line.decode("utf-8","replace").strip()[-1500:]
        threading.Thread(target=err,daemon=True).start()
        while ffmpeg_proc and ffmpeg_proc.poll() is None:
            chunk=ffmpeg_proc.stdout.read(4096)
            if not chunk:break
            state["decoded_frames"]+=chunk.count(b"--frame")
            broadcast_ffmpeg(chunk)
    except Exception as e:state["ffmpeg"]="error";state["last_error"]=repr(e)
    finally:
        if ffmpeg_proc:
            try:ffmpeg_proc.kill()
            except Exception:pass
        ffmpeg_proc=None


def feed_ffmpeg(packet):
    if ffmpeg_proc and ffmpeg_proc.stdin:
        try:ffmpeg_proc.stdin.write(packet);ffmpeg_proc.stdin.flush()
        except Exception as e:state["last_error"]=f"FFmpeg stdin: {e}"


def stream_loop(port, path):
    if not PASSWORD:state["last_error"]="CAMERA_PASSWORD is not set";return
    threading.Thread(target=ffmpeg_loop,daemon=True).start()
    ws=None
    try:
        ws_url=make_url(port,path)
        realm,nonce,qop=get_challenge(ws_url,port)
        ws=open_authenticated(ws_url,port,make_digest(ws_url,realm,nonce,qop))
        state["authenticated"]=True;first=True
        while not stop_event.is_set():
            packet=ws.recv()
            if packet is None:raise RuntimeError("WebSocket closed")
            if isinstance(packet,str):
                state["last_error"]=packet
                if '"errorCode":401' in packet:raise RuntimeError(packet)
                continue
            state["packets"]+=1;state["bytes"]+=len(packet);state["last_packet"]=f"{len(packet)} bytes"
            if first:
                if not packet.startswith(b"FLV"):raise RuntimeError("First media packet is not FLV")
                first=False;state["player"]="streaming"
            state["flv_bytes"]+=len(packet)
            with parser_lock:
                if len(stream_prefix)<MAX_PREFIX:stream_prefix.extend(packet[:MAX_PREFIX-len(stream_prefix)])
            parse_flv_incremental(packet);feed_ffmpeg(packet);broadcast(packet)
        try:ws.close()
        except Exception:pass
    except Exception as e:state["last_error"]=repr(e);state["player"]="stopped"


def stop_current():
    global stream_thread,ffmpeg_proc
    stop_event.set()
    if ffmpeg_proc:
        try:
            if ffmpeg_proc.stdin:ffmpeg_proc.stdin.close()
        except Exception:pass
        try:ffmpeg_proc.kill()
        except Exception:pass
        ffmpeg_proc=None
    if stream_thread and stream_thread.is_alive():stream_thread.join(timeout=2)
    stop_event.clear()


def start_stream(port, path):
    global stream_thread
    stop_current();reset_state(port,path)
    stream_thread=threading.Thread(target=stream_loop,args=(port,path),daemon=True);stream_thread.start()


@app.get('/')
def index():return render_template_string(PAGE,presets=PRESETS,default_path=DEFAULT_PATH,default_port=DEFAULT_PORT)

@app.get('/status')
def status():
    with clients_lock:state["stream_clients"]=len(clients)
    return jsonify(state)

@app.get('/switch')
def switch():
    path=request.args.get('path','').strip()
    try:port=int(request.args.get('port',str(DEFAULT_PORT)))
    except ValueError:return jsonify(ok=False,error='Invalid port'),400
    if not (1<=port<=65535):return jsonify(ok=False,error='Invalid port'),400
    if not path.startswith('/') or len(path)>200:return jsonify(ok=False,error='Invalid path'),400
    start_stream(port,path);return jsonify(ok=True,path=path,port=port,url=make_url(port,path))

@app.get('/live.flv')
def live_flv():
    class Client:
        def __init__(self):self.q=[];self.cv=threading.Condition()
        def write(self,b):
            with self.cv:self.q.append(b);self.cv.notify()
    c=Client()
    with parser_lock:prefix=bytes(stream_prefix)
    with clients_lock:clients.append(c)
    def gen():
        try:
            if prefix:yield prefix
            while True:
                with c.cv:
                    while not c.q:c.cv.wait(timeout=15)
                    b=c.q.pop(0)
                if b is not None:yield b
        finally:
            with clients_lock:
                if c in clients:clients.remove(c)
    return Response(gen(),mimetype='video/x-flv',headers={'Cache-Control':'no-cache,no-store','Pragma':'no-cache','Access-Control-Allow-Origin':'*','X-Accel-Buffering':'no'})

@app.get('/live.mjpg')
def live_mjpg():
    class Client:
        def __init__(self):self.q=[];self.cv=threading.Condition()
        def write(self,b):
            with self.cv:self.q.append(b);self.cv.notify()
    c=Client()
    with ffmpeg_clients_lock:ffmpeg_clients.append(c)
    def gen():
        try:
            while True:
                with c.cv:
                    while not c.q:c.cv.wait(timeout=15)
                    b=c.q.pop(0)
                if b is not None:yield b
        finally:
            with ffmpeg_clients_lock:
                if c in ffmpeg_clients:ffmpeg_clients.remove(c)
    return Response(gen(),mimetype='multipart/x-mixed-replace; boundary=frame',headers={'Cache-Control':'no-cache,no-store','Pragma':'no-cache'})


def main():
    print('='*70);print('Uniview Multi-Camera / Multi-Stream Viewer');print('='*70)
    start_stream(DEFAULT_PORT,DEFAULT_PATH)
    url=f'http://127.0.0.1:{LOCAL_PORT}/';threading.Timer(1.0,lambda:webbrowser.open(url)).start();app.run(host='127.0.0.1',port=LOCAL_PORT,debug=False,threaded=True)

if __name__=='__main__':main()
