import hashlib
import json
import os
import re
import threading
import webbrowser

import websocket
from flask import Flask, Response, jsonify, render_template_string

HOST = os.getenv("CAMERA_HOST", "37.202.152.217")
PORT = int(os.getenv("CAMERA_PORT", "8001"))
PATH = os.getenv("CAMERA_WS_PATH", "/media/flv/video2")
USERNAME = os.getenv("CAMERA_USERNAME", "admin")
PASSWORD = os.getenv("CAMERA_PASSWORD", "")
LOCAL_PORT = int(os.getenv("LOCAL_PORT", "5050"))
WS_URL = f"ws://{HOST}:{PORT}{PATH}"
ORIGIN = f"http://{HOST}:{PORT}"

app = Flask(__name__)
state = {"connected": False, "authenticated": False, "packets": 0, "bytes": 0,
         "last_packet": "", "last_error": "", "challenge": "", "flv_bytes": 0,
         "player": "starting", "video_tags": 0, "keyframes": 0}

clients = []
clients_lock = threading.Lock()
stream_lock = threading.Lock()
stream_prefix = bytearray()          # FLV header + initial metadata/config tags
recent = bytearray()                 # rolling FLV bytes for diagnostics
MAX_RECENT = 8 * 1024 * 1024
last_keyframe_stream_offset = None
last_sequence_tag = None
stream_total = 0

PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>Uniview Live</title>
<style>body{background:#111;color:#eee;font-family:Arial;margin:20px}video{width:min(100%,1280px);background:#000}pre{background:#222;padding:12px}</style></head>
<body><h2>Uniview WebSocket → FLV</h2><video id="v" controls autoplay muted playsinline></video><pre id="s">connecting...</pre>
<script src="https://cdn.jsdelivr.net/npm/mpegts.js@1.8.0/dist/mpegts.min.js"></script>
<script>
const v=document.getElementById('v');
if(mpegts.isSupported()){
 const p=mpegts.createPlayer({type:'flv',url:'/live.flv',isLive:true,hasAudio:false});
 p.on(mpegts.Events.ERROR,(t,d,i)=>console.log('mpegts error',t,d,i));
 p.attachMediaElement(v);p.load();p.play().catch(()=>{});
}else{document.getElementById('s').textContent='MSE/FLV is not supported by this browser';}
async function st(){try{let r=await fetch('/status');document.getElementById('s').textContent=JSON.stringify(await r.json(),null,2)}catch(e){}}setInterval(st,1000);st();
</script></body></html>'''


def md5(v):
    return hashlib.md5(v.encode()).hexdigest()


def parse_challenge(text):
    m = re.search(r"realm=([^,\s]+).*?nonce=([^,\s]+).*?qop=([^,\s]+)", text)
    if not m:
        raise RuntimeError("Digest challenge not found")
    return m.group(1), m.group(2), m.group(3)


def make_digest(realm, nonce, qop):
    uri = WS_URL
    nc = "00000001"
    cnonce = os.urandom(16).hex()
    ha1 = md5(f"{USERNAME}:{realm}:{PASSWORD}")
    ha2 = md5(f"GET:{uri}")
    response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return ('Digest ' + f'username="{USERNAME}", realm="{realm}", nonce="{nonce}", '
            f'algorithm="MD5", uri="{uri}", response="{response}", qop="{qop}", '
            f'nc="{nc}", cnonce="{cnonce}"')


def get_challenge():
    ws = websocket.create_connection(WS_URL, origin=ORIGIN, timeout=10)
    state["connected"] = True
    msg = ws.recv(); ws.close()
    if isinstance(msg, bytes):
        raise RuntimeError("Binary data before authentication")
    state["challenge"] = msg
    data = json.loads(msg)
    if data.get("errorCode") != 401:
        raise RuntimeError(msg)
    return parse_challenge(data["detail"])


def open_authenticated(auth):
    return websocket.create_connection(WS_URL, origin=ORIGIN, timeout=15, header=[
        "Pragma: no-cache", "Cache-Control: no-cache",
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Cookie: langInfo_=1; noShowTip=1; Authorization=" + auth,
    ])


def parse_flv_tags(data, base_offset):
    global last_keyframe_stream_offset, last_sequence_tag
    # Called with arbitrary chunks. Only parse complete FLV tags.
    pos = 13 if base_offset == 0 and data.startswith(b"FLV") else 0
    if pos == 13 and len(data) >= 13:
        prev = int.from_bytes(data[9:13], "big")
        pos = 13
    while pos + 11 <= len(data):
        tag_type = data[pos]
        size = int.from_bytes(data[pos+1:pos+4], "big")
        end = pos + 11 + size + 4
        if end > len(data):
            break
        if tag_type == 9 and size >= 2:
            video = data[pos+11:pos+11+size]
            codec = video[0] & 0x0f
            frame = video[0] >> 4
            avc_type = video[1] if codec == 7 else None
            state["video_tags"] += 1
            if codec == 7 and avc_type == 0:
                last_sequence_tag = bytes(data[pos:end])
            if codec == 7 and frame == 1 and avc_type == 1:
                state["keyframes"] += 1
                last_keyframe_stream_offset = base_offset + pos
        pos = end


def broadcast(data):
    dead=[]
    with clients_lock:
        for c in clients:
            try: c.write(data)
            except Exception: dead.append(c)
        for c in dead:
            if c in clients: clients.remove(c)


def feed_stream(packet):
    global stream_total, recent, stream_prefix
    with stream_lock:
        offset = stream_total
        stream_total += len(packet)
        if len(stream_prefix) < 256 * 1024:
            stream_prefix.extend(packet[:max(0, 256 * 1024 - len(stream_prefix))])
        recent.extend(packet)
        if len(recent) > MAX_RECENT:
            del recent[:len(recent)-MAX_RECENT]
        state["flv_bytes"] = stream_total
        # Analyze only the prefix and current packet; the packet boundaries are not FLV boundaries.
        sample = bytes(stream_prefix)
        if sample.startswith(b"FLV"):
            parse_flv_tags(sample, 0)


def initial_payload():
    with stream_lock:
        # Replay the complete beginning of the stream. This fixes the race where the
        # WebSocket receives the FLV header before mpegts.js connects to /live.flv.
        return bytes(stream_prefix)


def stream_loop():
    if not PASSWORD:
        state["last_error"] = "CAMERA_PASSWORD is not set"; return
    try:
        realm, nonce, qop = get_challenge()
        ws = open_authenticated(make_digest(realm, nonce, qop))
        state["authenticated"] = True
        first = True
        while True:
            packet = ws.recv()
            if packet is None: raise RuntimeError("WebSocket closed")
            if isinstance(packet, str):
                state["last_error"] = packet
                if '"errorCode":401' in packet: raise RuntimeError(packet)
                continue
            state["packets"] += 1
            state["bytes"] += len(packet)
            state["last_packet"] = f"{len(packet)} bytes"
            if first:
                if not packet.startswith(b"FLV"):
                    raise RuntimeError("First media packet is not FLV")
                first = False
                state["player"] = "streaming"
            feed_stream(packet)
            broadcast(packet)
        ws.close()
    except Exception as e:
        state["last_error"] = repr(e); state["player"] = "stopped"


@app.get('/')
def index(): return render_template_string(PAGE)

@app.get('/status')
def status(): return jsonify(state)

@app.get('/live.flv')
def live_flv():
    def gen():
        class Client:
            def __init__(self): self.q=[]; self.cv=threading.Condition()
            def write(self,b):
                with self.cv: self.q.append(b); self.cv.notify()
        c=Client()
        prefix = initial_payload()
        # Always start a browser client with a complete FLV beginning.
        if prefix:
            yield prefix
        with clients_lock: clients.append(c)
        try:
            while True:
                with c.cv:
                    while not c.q: c.cv.wait(timeout=15)
                    if not c.q: continue
                    b=c.q.pop(0)
                yield b
        finally:
            with clients_lock:
                if c in clients: clients.remove(c)
    return Response(gen(), mimetype='video/x-flv', headers={'Cache-Control':'no-cache','Access-Control-Allow-Origin':'*'})


def main():
    print('='*70); print('Uniview WebSocket → live FLV viewer'); print('Endpoint:', WS_URL); print('='*70)
    threading.Thread(target=stream_loop, daemon=True).start()
    url=f'http://127.0.0.1:{LOCAL_PORT}/'
    threading.Timer(1.0, lambda:webbrowser.open(url)).start()
    app.run(host='127.0.0.1', port=LOCAL_PORT, debug=False, threaded=True)

if __name__ == '__main__': main()
