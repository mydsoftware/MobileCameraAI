import hashlib
import json
import os
import re
import shutil
import subprocess
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
state = {
    "connected": False, "authenticated": False, "packets": 0, "bytes": 0,
    "last_packet": "", "last_error": "", "challenge": "", "flv_bytes": 0,
    "player": "starting", "video_tags": 0, "keyframes": 0,
    "audio_tags": 0, "flv_header_ok": False, "stream_clients": 0,
    "ffmpeg": "starting", "decoded_frames": 0,
}

clients = []
clients_lock = threading.Lock()
ffmpeg_clients = []
ffmpeg_clients_lock = threading.Lock()
parser_lock = threading.Lock()
parser_buffer = bytearray()
stream_prefix = bytearray()
MAX_PREFIX = 4 * 1024 * 1024
ffmpeg_proc = None

PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>Uniview Live</title>
<style>body{background:#111;color:#eee;font-family:Arial;margin:20px}img{width:min(100%,1280px);background:#000;display:block}pre{background:#222;padding:12px}</style></head>
<body><h2>Uniview Live</h2><img id="m" src="/live.mjpg" alt="loading..."><pre id="s">connecting...</pre>
<script>async function st(){try{let r=await fetch('/status');document.getElementById('s').textContent=JSON.stringify(await r.json(),null,2)}catch(e){}}setInterval(st,1000);st();</script></body></html>'''


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


def broadcast(data):
    dead = []
    with clients_lock:
        for c in clients:
            try: c.write(data)
            except Exception: dead.append(c)
        for c in dead:
            if c in clients: clients.remove(c)


def broadcast_ffmpeg(data):
    dead = []
    with ffmpeg_clients_lock:
        for c in ffmpeg_clients:
            try: c.write(data)
            except Exception: dead.append(c)
        for c in dead:
            if c in ffmpeg_clients: ffmpeg_clients.remove(c)


def parse_flv_incremental(chunk):
    global parser_buffer
    with parser_lock:
        parser_buffer.extend(chunk)
        if len(parser_buffer) > MAX_PREFIX:
            del parser_buffer[:-MAX_PREFIX]
        data = parser_buffer
        if data.startswith(b"FLV") and len(data) >= 13:
            state["flv_header_ok"] = True
            pos = 13
            # FLV tag: 11-byte header + payload + 4-byte PreviousTagSize.
            while pos + 15 <= len(data):
                tag_type = data[pos]
                size = int.from_bytes(data[pos + 1:pos + 4], "big")
                end = pos + 11 + size + 4
                if end > len(data):
                    break
                if tag_type == 9:
                    state["video_tags"] += 1
                    if size >= 2:
                        v = data[pos + 11:pos + 11 + size]
                        codec = v[0] & 0x0F
                        frame = v[0] >> 4
                        if codec == 7 and len(v) >= 2 and v[1] == 1 and frame == 1:
                            state["keyframes"] += 1
                elif tag_type == 8:
                    state["audio_tags"] += 1
                pos = end
            # Keep only the unconsumed tail, so tags are counted once.
            if pos > 13:
                del parser_buffer[:pos]


def ffmpeg_loop():
    global ffmpeg_proc
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        state["ffmpeg"] = "NOT_INSTALLED"
        state["last_error"] = "FFmpeg not found in PATH"
        return
    try:
        ffmpeg_proc = subprocess.Popen([
            ffmpeg, "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts", "-probesize", "20M", "-analyzeduration", "20M",
            "-f", "flv", "-i", "pipe:0", "-an",
            "-c:v", "mjpeg", "-q:v", "5", "-f", "mpjpeg",
            "-boundary_tag", "frame", "pipe:1"
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        state["ffmpeg"] = "running"

        def stderr_reader():
            while ffmpeg_proc and ffmpeg_proc.poll() is None:
                line = ffmpeg_proc.stderr.readline()
                if line:
                    state["last_error"] = line.decode("utf-8", "replace").strip()[-1500:]
        threading.Thread(target=stderr_reader, daemon=True).start()

        while ffmpeg_proc.poll() is None:
            chunk = ffmpeg_proc.stdout.read(4096)
            if not chunk:
                break
            state["decoded_frames"] += chunk.count(b"--frame")
            broadcast_ffmpeg(chunk)
    except Exception as e:
        state["ffmpeg"] = "error"
        state["last_error"] = repr(e)
    finally:
        if ffmpeg_proc:
            try: ffmpeg_proc.kill()
            except Exception: pass
        ffmpeg_proc = None


def feed_ffmpeg(packet):
    if ffmpeg_proc and ffmpeg_proc.stdin:
        try:
            ffmpeg_proc.stdin.write(packet)
            ffmpeg_proc.stdin.flush()
        except Exception as e:
            state["last_error"] = f"FFmpeg stdin: {e}"


def stream_loop():
    if not PASSWORD:
        state["last_error"] = "CAMERA_PASSWORD is not set"; return
    threading.Thread(target=ffmpeg_loop, daemon=True).start()
    try:
        realm, nonce, qop = get_challenge()
        ws = open_authenticated(make_digest(realm, nonce, qop))
        state["authenticated"] = True
        first = True
        while True:
            packet = ws.recv()
            if packet is None:
                raise RuntimeError("WebSocket closed")
            if isinstance(packet, str):
                state["last_error"] = packet
                if '"errorCode":401' in packet:
                    raise RuntimeError(packet)
                continue
            state["packets"] += 1
            state["bytes"] += len(packet)
            state["last_packet"] = f"{len(packet)} bytes"
            if first:
                if not packet.startswith(b"FLV"):
                    raise RuntimeError("First media packet is not FLV")
                first = False
                state["player"] = "streaming"
            state["flv_bytes"] += len(packet)
            if len(stream_prefix) < MAX_PREFIX:
                stream_prefix.extend(packet[:MAX_PREFIX-len(stream_prefix)])
            parse_flv_incremental(packet)
            feed_ffmpeg(packet)
            broadcast(packet)
        ws.close()
    except Exception as e:
        state["last_error"] = repr(e)
        state["player"] = "stopped"


@app.get('/')
def index():
    return render_template_string(PAGE)


@app.get('/status')
def status():
    with clients_lock:
        state["stream_clients"] = len(clients)
    return jsonify(state)


@app.get('/live.flv')
def live_flv():
    class Client:
        def __init__(self):
            self.q = []
            self.cv = threading.Condition()
        def write(self, b):
            with self.cv:
                self.q.append(b)
                self.cv.notify()

    c = Client()
    with parser_lock:
        prefix = bytes(stream_prefix)
    with clients_lock:
        clients.append(c)

    def gen():
        try:
            if prefix:
                yield prefix
            while True:
                with c.cv:
                    while not c.q:
                        c.cv.wait(timeout=15)
                    b = c.q.pop(0)
                if b is not None:
                    yield b
        finally:
            with clients_lock:
                if c in clients:
                    clients.remove(c)

    return Response(gen(), mimetype='video/x-flv', headers={
        'Cache-Control': 'no-cache,no-store', 'Pragma': 'no-cache',
        'Access-Control-Allow-Origin': '*', 'X-Accel-Buffering': 'no'
    })


@app.get('/live.mjpg')
def live_mjpg():
    class Client:
        def __init__(self):
            self.q = []
            self.cv = threading.Condition()
        def write(self, b):
            with self.cv:
                self.q.append(b)
                self.cv.notify()

    c = Client()
    with ffmpeg_clients_lock:
        ffmpeg_clients.append(c)

    def gen():
        try:
            while True:
                with c.cv:
                    while not c.q:
                        c.cv.wait(timeout=15)
                    b = c.q.pop(0)
                if b is not None:
                    yield b
        finally:
            with ffmpeg_clients_lock:
                if c in ffmpeg_clients:
                    ffmpeg_clients.remove(c)

    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame', headers={
        'Cache-Control': 'no-cache,no-store', 'Pragma': 'no-cache'
    })


def main():
    print('=' * 70)
    print('Uniview WebSocket -> FLV -> FFmpeg -> MJPEG')
    print('Endpoint:', WS_URL)
    print('=' * 70)
    threading.Thread(target=stream_loop, daemon=True).start()
    url = f'http://127.0.0.1:{LOCAL_PORT}/'
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host='127.0.0.1', port=LOCAL_PORT, debug=False, threaded=True)


if __name__ == '__main__':
    main()
