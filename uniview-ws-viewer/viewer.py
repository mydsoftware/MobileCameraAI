import hashlib
import json
import os
import pathlib
import re
import struct
import threading
import time
import webbrowser

import websocket
from flask import Flask, jsonify, render_template_string, send_file

HOST = os.getenv("CAMERA_HOST", "37.202.152.217")
PORT = int(os.getenv("CAMERA_PORT", "8001"))
PATH = os.getenv("CAMERA_WS_PATH", "/media/flv/video2")
USERNAME = os.getenv("CAMERA_USERNAME", "admin")
PASSWORD = os.getenv("CAMERA_PASSWORD", "")
WEB_LOGIN_HANDLE = os.getenv("WEB_LOGIN_HANDLE", "")
LOCAL_PORT = int(os.getenv("LOCAL_PORT", "5050"))
CAPTURE_DIR = pathlib.Path("captures")
CAPTURE_DIR.mkdir(exist_ok=True)

WS_URL = f"ws://{HOST}:{PORT}{PATH}"
ORIGIN = f"http://{HOST}:{PORT}"

app = Flask(__name__)
state = {
    "connected": False,
    "authenticated": False,
    "packets": 0,
    "bytes": 0,
    "last_packet": "",
    "last_error": "",
    "challenge": "",
    "analysis": {},
}

PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>Uniview WS Analyzer</title>
<style>body{font-family:Arial;background:#111;color:#eee;margin:30px}pre{background:#222;padding:15px;border-radius:8px;white-space:pre-wrap}button{padding:10px 16px;margin-right:8px}</style>
</head><body><h1>Uniview WebSocket Analyzer</h1><p><code>{{ url }}</code></p>
<pre id="s">loading...</pre><button onclick="location.href='/analyze'">Analyze capture</button><a href="/download-capture" style="color:#8cf"> Download capture</a>
<script>async function refresh(){const r=await fetch('/status');document.getElementById('s').textContent=JSON.stringify(await r.json(),null,2)}setInterval(refresh,1000);refresh();</script></body></html>
"""


def md5(value):
    return hashlib.md5(value.encode()).hexdigest()


def parse_challenge(text):
    m = re.search(r"realm=([^,\s]+).*?nonce=([^,\s]+).*?qop=([^,\s]+)", text)
    if not m:
        raise RuntimeError(f"Cannot parse Digest challenge: {text}")
    return m.group(1), m.group(2), m.group(3)


def make_digest(realm, nonce, qop="auth"):
    uri = WS_URL
    nc = "00000001"
    cnonce = os.urandom(16).hex()
    ha1 = md5(f"{USERNAME}:{realm}:{PASSWORD}")
    ha2 = md5(f"GET:{uri}")
    response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return (
        'Digest '
        f'username="{USERNAME}", realm="{realm}", nonce="{nonce}", '
        'algorithm="MD5", '
        f'uri="{uri}", response="{response}", qop="{qop}", '
        f'nc="{nc}", cnonce="{cnonce}"'
    )


def initial_challenge():
    ws = websocket.create_connection(WS_URL, origin=ORIGIN, timeout=10)
    state["connected"] = True
    msg = ws.recv()
    ws.close()
    if isinstance(msg, bytes):
        raise RuntimeError("Binary data before authentication")
    state["challenge"] = msg
    data = json.loads(msg)
    if data.get("errorCode") != 401:
        raise RuntimeError(f"Unexpected response: {msg}")
    return parse_challenge(data.get("detail", ""))


def authenticated_connect(auth_cookie):
    cookies = [f"Authorization={auth_cookie}"]
    if WEB_LOGIN_HANDLE:
        cookies.append(f"WebLoginHandle={WEB_LOGIN_HANDLE}")
    cookies += ["langInfo_=1", "noShowTip=1"]
    return websocket.create_connection(
        WS_URL,
        origin=ORIGIN,
        timeout=15,
        header=[
            "Pragma: no-cache",
            "Cache-Control: no-cache",
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
            "Cookie: " + "; ".join(cookies),
        ],
    )


def find_signatures(data):
    signatures = {
        b"FLV": "FLV",
        b"\x1a\x45\xdf\xa3": "EBML/WebM",
        b"ftyp": "MP4/fMP4",
        b"\x00\x00\x00\x01\x67": "H264 SPS",
        b"\x00\x00\x01\x67": "H264 SPS",
        b"\x00\x00\x00\x01\x65": "H264 IDR",
        b"\x00\x00\x01\x65": "H264 IDR",
        b"\x00\x00\x00\x01\x68": "H264 PPS",
        b"\x00\x00\x01\x68": "H264 PPS",
    }
    out = {}
    for sig, name in signatures.items():
        positions = []
        start = 0
        while True:
            p = data.find(sig, start)
            if p < 0:
                break
            positions.append(p)
            start = p + 1
            if len(positions) >= 20:
                break
        if positions:
            out[name] = positions
    return out


def analyze_capture():
    files = sorted(CAPTURE_DIR.glob("packet_*.bin"))
    if not files:
        return {"error": "No capture packets"}
    packets = []
    combined = bytearray()
    for p in files:
        b = p.read_bytes()
        packets.append({"file": p.name, "size": len(b), "first32": b[:32].hex(" "), "last16": b[-16:].hex(" ")})
        combined.extend(b)
    result = {
        "packet_count": len(files),
        "total_bytes": len(combined),
        "first_packet": packets[0],
        "last_packet": packets[-1],
        "signatures_in_concatenated_payload": find_signatures(bytes(combined)),
        "first_64_combined": bytes(combined[:64]).hex(" "),
        "last_64_combined": bytes(combined[-64:]).hex(" "),
        "size_min": min(x["size"] for x in packets),
        "size_max": max(x["size"] for x in packets),
        "size_avg": round(sum(x["size"] for x in packets) / len(packets), 2),
    }
    state["analysis"] = result
    pathlib.Path("capture_analysis.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def capture_loop():
    if not PASSWORD:
        state["last_error"] = "CAMERA_PASSWORD is not set"
        return
    try:
        realm, nonce, qop = initial_challenge()
        auth_cookie = make_digest(realm, nonce, qop)
        ws = authenticated_connect(auth_cookie)
        state["authenticated"] = True
        index = 0
        while index < 1000:
            packet = ws.recv()
            if packet is None:
                raise RuntimeError("WebSocket closed")
            if isinstance(packet, str):
                state["last_error"] = packet
                if "errorCode" in packet:
                    break
                continue
            index += 1
            state["packets"] = index
            state["bytes"] += len(packet)
            state["last_packet"] = f"{len(packet)} bytes"
            (CAPTURE_DIR / f"packet_{index:06d}.bin").write_bytes(packet)
            if index % 50 == 0:
                analyze_capture()
        ws.close()
        analyze_capture()
    except Exception as exc:
        state["last_error"] = repr(exc)


@app.get("/")
def index():
    return render_template_string(PAGE, url=WS_URL)


@app.get("/status")
def status():
    return jsonify(state)


@app.get("/analyze")
def analyze():
    return jsonify(analyze_capture())


@app.get("/download-capture")
def download_capture():
    path = pathlib.Path("capture.bin")
    with path.open("wb") as out:
        for p in sorted(CAPTURE_DIR.glob("packet_*.bin")):
            out.write(p.read_bytes())
    return send_file(path, as_attachment=True, download_name="uniview-capture.bin")


def main():
    print("=" * 70)
    print("Uniview WebSocket Analyzer")
    print("=" * 70)
    print("Endpoint:", WS_URL)
    print("Capture directory:", CAPTURE_DIR.resolve())
    if not PASSWORD:
        print("Set CAMERA_PASSWORD before running.")
        return
    threading.Thread(target=capture_loop, daemon=True).start()
    url = f"http://127.0.0.1:{LOCAL_PORT}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=LOCAL_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
