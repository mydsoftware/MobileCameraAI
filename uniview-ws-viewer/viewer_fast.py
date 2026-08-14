import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time

import websocket
from flask import Flask, Response, jsonify

HOST = os.getenv("CAMERA_HOST", "37.202.152.217")
PORT = int(os.getenv("CAMERA_WS_PORT", "8001"))
PATH = os.getenv("CAMERA_WS_PATH", "/media/flv/video1")
USERNAME = os.getenv("CAMERA_USERNAME", "admin")
PASSWORD = os.getenv("CAMERA_PASSWORD", "")
WEB_PORT = int(os.getenv("LOCAL_PORT", "5050"))
FPS = os.getenv("VIEWER_FPS", "20")
WIDTH = os.getenv("VIEWER_WIDTH", "1280")
QUALITY = os.getenv("VIEWER_QUALITY", "8")

app = Flask(__name__)
proc = None
ws = None
stop = threading.Event()
clients = []
clients_lock = threading.Lock()
state = {"connected": False, "authenticated": False, "streaming": False, "ffmpeg": "stopped", "packets": 0, "bytes": 0, "frames": 0, "last_error": ""}


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def digest_challenge(text):
    d = json.loads(text)
    if d.get("errorCode") != 401:
        raise RuntimeError(f"Expected 401 challenge: {text}")
    detail = d.get("detail", "")
    def get(name):
        m = re.search(rf'{name}="?([^,\s"]+)', detail, re.I)
        if not m:
            raise RuntimeError(f"Missing digest field: {name}")
        return m.group(1)
    return get("realm"), get("nonce"), get("qop")


def make_auth(uri, realm, nonce, qop):
    nc = "00000001"
    cnonce = os.urandom(12).hex()
    ha1 = md5(f"{USERNAME}:{realm}:{PASSWORD}")
    ha2 = md5(f"GET:{uri}")
    response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return (f'Digest username="{USERNAME}", realm="{realm}", nonce="{nonce}", '
            f'algorithm="MD5", uri="{uri}", response="{response}", '
            f'qop="{qop}", nc="{nc}", cnonce="{cnonce}"')


def open_ws():
    global ws
    url = f"ws://{HOST}:{PORT}{PATH}"
    challenge_ws = websocket.create_connection(
        url, origin=f"http://{HOST}:{PORT}", timeout=8, compression=None,
        header=["User-Agent: Mozilla/5.0 Chrome/151 Safari/537.36"]
    )
    try:
        first = challenge_ws.recv()
        if isinstance(first, bytes):
            raise RuntimeError("Camera sent media before authentication")
        realm, nonce, qop = digest_challenge(first)
    finally:
        challenge_ws.close()

    auth = make_auth(url, realm, nonce, qop)
    # Uniview's WebSocket endpoint accepts the Digest value through its
    # Authorization cookie. This is the form used by the working viewer.
    ws = websocket.create_connection(
        url,
        origin=f"http://{HOST}:{PORT}",
        timeout=20,
        compression=None,
        header=[
            "Pragma: no-cache",
            "Cache-Control: no-cache",
            "User-Agent: Mozilla/5.0 Chrome/151 Safari/537.36",
            "Cookie: langInfo_=1; noShowTip=1; Authorization=" + auth,
        ],
    )
    state.update(connected=True, authenticated=True, last_error="")
    return ws


def broadcast(data):
    dead = []
    with clients_lock:
        for c in clients:
            try:
                c.push(data)
            except Exception:
                dead.append(c)
        for c in dead:
            if c in clients:
                clients.remove(c)


def ffmpeg_errors():
    while proc and proc.poll() is None:
        line = proc.stderr.readline()
        if line:
            state["last_error"] = line.decode("utf-8", "replace").strip()[-1000:]


def ffmpeg_loop():
    global proc
    binary = shutil.which("ffmpeg")
    if not binary:
        state.update(ffmpeg="missing", last_error="ffmpeg not found")
        return
    vf = f"fps={FPS},scale='min({WIDTH},iw)':-2:force_original_aspect_ratio=decrease"
    cmd = [binary, "-hide_banner", "-loglevel", "warning",
           "-fflags", "nobuffer+genpts", "-flags", "low_delay",
           "-f", "flv", "-i", "pipe:0", "-an",
           "-vf", vf, "-c:v", "mjpeg", "-q:v", QUALITY,
           "-flush_packets", "1", "-f", "mpjpeg", "-boundary_tag", "frame", "pipe:1"]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        state["ffmpeg"] = "running"
        threading.Thread(target=ffmpeg_errors, daemon=True).start()
        while proc and proc.poll() is None:
            data = proc.stdout.read(65536)
            if not data:
                break
            state["frames"] += data.count(b"--frame")
            broadcast(data)
    except Exception as e:
        state.update(ffmpeg="error", last_error=repr(e))


def feed(data):
    if proc and proc.stdin:
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except Exception as e:
            state["last_error"] = repr(e)


def stream_loop():
    threading.Thread(target=ffmpeg_loop, daemon=True).start()
    try:
        camera = open_ws()
        state["streaming"] = True
        while not stop.is_set():
            packet = camera.recv()
            if isinstance(packet, str):
                if '"errorCode":401' in packet:
                    raise RuntimeError(packet)
                continue
            if not packet:
                continue
            state["packets"] += 1
            state["bytes"] += len(packet)
            feed(packet)
    except Exception as e:
        state.update(streaming=False, last_error=repr(e))
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        state["streaming"] = False


class Client:
    def __init__(self):
        self.cv = threading.Condition()
        self.latest = None
        self.closed = False

    def push(self, data):
        with self.cv:
            self.latest = data
            self.cv.notify()

    def generate(self):
        try:
            while not self.closed:
                with self.cv:
                    if self.latest is None:
                        self.cv.wait(timeout=5)
                    data = self.latest
                    self.latest = None
                if data:
                    yield data
        finally:
            self.closed = True


@app.get("/")
def index():
    return f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Uniview Fast Live</title><style>body{{margin:0;background:#111;color:#eee;font-family:Arial;text-align:center}}img{{width:100%;height:auto;display:block;background:#000}}pre{{text-align:left;margin:8px;padding:8px;background:#222}}</style></head><body><h3>Uniview Fast Live</h3><img src="/live.mjpg"><pre id="s">loading...</pre><script>async function s(){{try{{let r=await fetch('/status');document.getElementById('s').textContent=JSON.stringify(await r.json(),null,2)}}catch(e){{}}}}setInterval(s,1000);s();</script></body></html>'''


@app.get("/status")
def status():
    r = dict(state)
    with clients_lock:
        r["clients"] = len(clients)
    r.update({"host": HOST, "port": PORT, "path": PATH, "fps": FPS, "width": WIDTH, "quality": QUALITY})
    return jsonify(r)


@app.get("/live.mjpg")
def live():
    c = Client()
    with clients_lock:
        clients.append(c)
    def gen():
        try:
            yield from c.generate()
        finally:
            with clients_lock:
                if c in clients:
                    clients.remove(c)
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame", headers={"Cache-Control":"no-cache,no-store","Pragma":"no-cache"})


def main():
    print(f"Fast viewer: ws://{HOST}:{PORT}{PATH}")
    print(f"Web: http://127.0.0.1:{WEB_PORT}")
    print(f"FPS={FPS} WIDTH={WIDTH} QUALITY={QUALITY}")
    threading.Thread(target=stream_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
