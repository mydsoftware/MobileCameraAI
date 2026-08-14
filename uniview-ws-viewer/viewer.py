import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time

import websocket
from flask import Flask, Response, jsonify, render_template_string, request

HOST = os.getenv("CAMERA_HOST", "37.202.152.217")
USERNAME = os.getenv("CAMERA_USERNAME", "admin")
PASSWORD = os.getenv("CAMERA_PASSWORD", "")
DEFAULT_PORT = int(os.getenv("CAMERA_WS_PORT", "8001"))
DEFAULT_PATH = os.getenv("CAMERA_WS_PATH", "/media/flv/video1")
LOCAL_PORT = int(os.getenv("LOCAL_PORT", "5050"))

PRESETS = {
    "Camera 1 / Main": (8001, "/media/flv/video1"),
    "Camera 1 / Sub": (8001, "/media/flv/video2"),
    "Camera 1 / Third": (8001, "/media/flv/video3"),
    "Camera 2 / Main": (8002, "/media/flv/video1"),
    "Camera 2 / Sub": (8002, "/media/flv/video2"),
    "Camera 2 / Third": (8002, "/media/flv/video3"),
    "Camera 2 / Main media2": (8002, "/media2/flv/video1"),
    "Camera 2 / Sub media2": (8002, "/media2/flv/video2"),
    "Camera 2 / Third media2": (8002, "/media2/flv/video3"),
    "Camera 3 / Main": (8003, "/media3/flv/video1"),
    "Camera 3 / Sub": (8003, "/media3/flv/video2"),
    "Camera 3 / Third": (8003, "/media3/flv/video3"),
}

app = Flask(__name__)

state = {
    "connected": False,
    "authenticated": False,
    "streaming": False,
    "ffmpeg": "stopped",
    "packets": 0,
    "bytes": 0,
    "decoded_frames": 0,
    "last_packet": "",
    "last_error": "",
    "challenge": "",
    "port": DEFAULT_PORT,
    "path": DEFAULT_PATH,
    "url": "",
}

state_lock = threading.Lock()
stop_event = threading.Event()
stream_thread = None
ffmpeg_proc = None
clients = []
clients_lock = threading.Lock()

PAGE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Uniview Live</title>
<style>
body{background:#111;color:#eee;font-family:Arial;margin:12px}.bar{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}
select,input,button{background:#222;color:#eee;border:1px solid #555;padding:9px;border-radius:5px}
img{width:100%;max-width:1280px;min-height:240px;background:#000;object-fit:contain;display:block}
pre{background:#222;padding:10px;white-space:pre-wrap}
</style></head><body>
<h2>Uniview Live - Python</h2>
<div class="bar"><select id="preset"></select><input id="port" type="number" value="8001"><input id="path" size="34"><button onclick="connect()">LIVE</button><button onclick="autoProbe()">Auto Detect</button></div>
<img id="live" src="/live.mjpg" alt="در حال دریافت تصویر...">
<pre id="status">در حال راه‌اندازی...</pre>
<script>
const presets={{presets|tojson}}, sel=document.getElementById('preset'),port=document.getElementById('port'),path=document.getElementById('path');
for(const [name,v] of Object.entries(presets)){let o=document.createElement('option');o.textContent=name;o.value=JSON.stringify(v);sel.appendChild(o)}
function apply(){let v=JSON.parse(sel.value);port.value=v[0];path.value=v[1]}
sel.onchange=apply;apply();
async function connect(){let r=await fetch('/switch?port='+encodeURIComponent(port.value)+'&path='+encodeURIComponent(path.value));let j=await r.json();if(!j.ok)alert(j.error);else document.getElementById('live').src='/live.mjpg?t='+Date.now()}
async function autoProbe(){let r=await fetch('/probe?port='+encodeURIComponent(port.value));let j=await r.json();document.getElementById('status').textContent=JSON.stringify(j,null,2);if(j.best){port.value=j.best.port;path.value=j.best.path;connect()}}
async function status(){try{let r=await fetch('/status');document.getElementById('status').textContent=JSON.stringify(await r.json(),null,2)}catch(e){}}
setInterval(status,1000);status();
</script></body></html>'''


def md5(value):
    return hashlib.md5(value.encode()).hexdigest()


def ws_url(port, path):
    return f"ws://{HOST}:{port}{path}"


def parse_challenge(text):
    data = json.loads(text)
    if data.get("errorCode") != 401:
        raise RuntimeError(f"Expected 401 challenge, got: {text}")
    detail = data.get("detail", "")
    realm = re.search(r"realm=\"?([^,\s\"]+)", detail)
    nonce = re.search(r"nonce=\"?([^,\s\"]+)", detail)
    qop = re.search(r"qop=\"?([^,\s\"]+)", detail)
    if not (realm and nonce and qop):
        raise RuntimeError("Digest challenge fields not found")
    return realm.group(1), nonce.group(1), qop.group(1)


def make_digest(uri, realm, nonce, qop):
    nc = "00000001"
    cnonce = os.urandom(16).hex()
    ha1 = md5(f"{USERNAME}:{realm}:{PASSWORD}")
    ha2 = md5(f"GET:{uri}")
    response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return (
        f'Digest username="{USERNAME}", realm="{realm}", nonce="{nonce}", '
        f'algorithm="MD5", uri="{uri}", response="{response}", '
        f'qop="{qop}", nc="{nc}", cnonce="{cnonce}"'
    )


def challenge(port, path):
    url = ws_url(port, path)
    ws = websocket.create_connection(
        url, origin=f"http://{HOST}:{port}", timeout=10,
        compression=None, enable_multithread=True
    )
    try:
        msg = ws.recv()
        if isinstance(msg, bytes):
            raise RuntimeError("Camera sent media before authentication")
        state["challenge"] = str(msg)
        return make_digest(url, *parse_challenge(msg))
    finally:
        ws.close()


def authenticated_ws(port, path):
    url = ws_url(port, path)
    auth = challenge(port, path)
    return websocket.create_connection(
        url, origin=f"http://{HOST}:{port}", timeout=20,
        compression=None, enable_multithread=True,
        header=[
            "Pragma: no-cache",
            "Cache-Control: no-cache",
            "User-Agent: Mozilla/5.0 Chrome/151 Safari/537.36",
            "Cookie: langInfo_=1; noShowTip=1; Authorization=" + auth,
        ],
    )


def set_state(**values):
    with state_lock:
        state.update(values)


def broadcast(data):
    dead = []
    with clients_lock:
        for client in clients:
            try:
                client.write(data)
            except Exception:
                dead.append(client)
        for client in dead:
            if client in clients:
                clients.remove(client)


def ffmpeg_worker():
    global ffmpeg_proc
    binary = shutil.which("ffmpeg")
    if not binary:
        set_state(ffmpeg="NOT_INSTALLED", last_error="FFmpeg در PATH پیدا نشد")
        return

    command = [
        binary, "-hide_banner", "-loglevel", "warning",
        "-fflags", "+genpts", "-f", "flv", "-i", "pipe:0",
        "-an", "-c:v", "mjpeg", "-q:v", "5",
        "-f", "mpjpeg", "-boundary_tag", "frame", "pipe:1",
    ]

    try:
        ffmpeg_proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0,
        )
        set_state(ffmpeg="running")

        def stderr_worker():
            while ffmpeg_proc and ffmpeg_proc.poll() is None:
                line = ffmpeg_proc.stderr.readline()
                if line:
                    set_state(last_error=line.decode("utf-8", "replace").strip()[-1500:])

        threading.Thread(target=stderr_worker, daemon=True).start()

        while ffmpeg_proc and ffmpeg_proc.poll() is None:
            data = ffmpeg_proc.stdout.read(8192)
            if not data:
                break
            set_state(decoded_frames=state["decoded_frames"] + data.count(b"--frame"))
            broadcast(data)
    except Exception as exc:
        set_state(ffmpeg="error", last_error=repr(exc))
    finally:
        try:
            if ffmpeg_proc:
                ffmpeg_proc.kill()
        except Exception:
            pass
        ffmpeg_proc = None


def feed_ffmpeg(data):
    if not ffmpeg_proc or not ffmpeg_proc.stdin:
        return
    try:
        ffmpeg_proc.stdin.write(data)
        ffmpeg_proc.stdin.flush()
    except Exception as exc:
        set_state(last_error=f"FFmpeg input: {exc}")


def stream_worker(port, path):
    if not PASSWORD:
        set_state(last_error="CAMERA_PASSWORD تنظیم نشده است")
        return

    threading.Thread(target=ffmpeg_worker, daemon=True).start()
    ws = None
    try:
        ws = authenticated_ws(port, path)
        set_state(connected=True, authenticated=True, streaming=True, last_error="")
        first = True

        while not stop_event.is_set():
            packet = ws.recv()
            if packet is None:
                raise RuntimeError("WebSocket connection closed")
            if isinstance(packet, str):
                set_state(last_error=packet)
                if '"errorCode":401' in packet:
                    raise RuntimeError(packet)
                continue

            if first:
                if not packet.startswith(b"FLV"):
                    raise RuntimeError(f"First packet is not FLV: {packet[:32]!r}")
                first = False

            set_state(
                packets=state["packets"] + 1,
                bytes=state["bytes"] + len(packet),
                last_packet=f"{len(packet)} bytes",
            )
            feed_ffmpeg(packet)
    except Exception as exc:
        set_state(streaming=False, last_error=repr(exc))
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        set_state(streaming=False)


def stop_stream():
    global stream_thread, ffmpeg_proc
    stop_event.set()
    if ffmpeg_proc:
        try:
            if ffmpeg_proc.stdin:
                ffmpeg_proc.stdin.close()
        except Exception:
            pass
        try:
            ffmpeg_proc.kill()
        except Exception:
            pass
    if stream_thread and stream_thread.is_alive():
        stream_thread.join(timeout=2)
    stop_event.clear()


def start_stream(port, path):
    global stream_thread
    stop_stream()
    with state_lock:
        for key in ("packets", "bytes", "decoded_frames"):
            state[key] = 0
        state.update({
            "connected": False, "authenticated": False, "streaming": False,
            "ffmpeg": "starting", "last_error": "", "port": port,
            "path": path, "url": ws_url(port, path),
        })
    stream_thread = threading.Thread(target=stream_worker, args=(port, path), daemon=True)
    stream_thread.start()


def probe_endpoint(port, path):
    try:
        url = ws_url(port, path)
        auth = challenge(port, path)
        ws = websocket.create_connection(
            url, origin=f"http://{HOST}:{port}", timeout=8,
            compression=None, enable_multithread=True,
            header=["User-Agent: Mozilla/5.0", "Authorization: " + auth],
        )
        try:
            deadline = time.time() + 3
            while time.time() < deadline:
                packet = ws.recv()
                if isinstance(packet, bytes):
                    return {"ok": packet.startswith(b"FLV"), "flv": packet.startswith(b"FLV"), "packet_size": len(packet), "port": port, "path": path, "url": url, "reason": ""}
        finally:
            ws.close()
        return {"ok": False, "port": port, "path": path, "url": url, "reason": "no binary FLV packet"}
    except Exception as exc:
        return {"ok": False, "port": port, "path": path, "url": ws_url(port, path), "reason": repr(exc)}


@app.get("/")
def index():
    return render_template_string(PAGE, presets=PRESETS)


@app.get("/status")
def status():
    with state_lock:
        result = dict(state)
    with clients_lock:
        result["stream_clients"] = len(clients)
    return jsonify(result)


@app.get("/switch")
def switch():
    path = request.args.get("path", "").strip()
    try:
        port = int(request.args.get("port", DEFAULT_PORT))
    except ValueError:
        return jsonify(ok=False, error="Invalid port"), 400
    if not 1 <= port <= 65535 or not path.startswith("/"):
        return jsonify(ok=False, error="Invalid port/path"), 400
    start_stream(port, path)
    return jsonify(ok=True, port=port, path=path, url=ws_url(port, path))


@app.get("/probe")
def probe():
    try:
        port = int(request.args.get("port", DEFAULT_PORT))
    except ValueError:
        return jsonify(ok=False, error="Invalid port"), 400

    candidates = [f"/media/flv/video{i}" for i in (1, 2, 3)]
    candidates += [f"/media2/flv/video{i}" for i in (1, 2, 3)]
    candidates += [f"/media3/flv/video{i}" for i in (1, 2, 3)]
    results = []
    for path in candidates:
        result = probe_endpoint(port, path)
        results.append(result)
        if result.get("ok"):
            return jsonify(ok=True, port=port, results=results, best=result)
    return jsonify(ok=True, port=port, results=results, best=None)


@app.get("/live.mjpg")
def live_mjpg():
    class Client:
        def __init__(self):
            self.queue = []
            self.cv = threading.Condition()

        def write(self, data):
            with self.cv:
                if len(self.queue) > 4:
                    self.queue.pop(0)
                self.queue.append(data)
                self.cv.notify()

    client = Client()
    with clients_lock:
        clients.append(client)

    def generate():
        try:
            while True:
                with client.cv:
                    if not client.queue:
                        client.cv.wait(timeout=15)
                    if not client.queue:
                        continue
                    data = client.queue.pop(0)
                yield data
        finally:
            with clients_lock:
                if client in clients:
                    clients.remove(client)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"},
    )


def main():
    print("=" * 60)
    print("Uniview Python Web Viewer")
    print(f"Camera: {HOST}:{DEFAULT_PORT}{DEFAULT_PATH}")
    print(f"Web:    http://127.0.0.1:{LOCAL_PORT}")
    print("=" * 60)
    start_stream(DEFAULT_PORT, DEFAULT_PATH)
    app.run(host="0.0.0.0", port=LOCAL_PORT, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
