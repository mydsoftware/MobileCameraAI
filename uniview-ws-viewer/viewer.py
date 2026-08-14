import base64
import hashlib
import json
import os
import pathlib
import threading
import time
import webbrowser

import websocket
from flask import Flask, jsonify, render_template_string

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
}

PAGE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Uniview WS Viewer</title>
<style>body{font-family:Arial;background:#111;color:#eee;margin:30px}pre{background:#222;padding:15px;border-radius:8px}h1{font-size:24px}</style>
</head><body>
<h1>Uniview WebSocket test</h1>
<p>Endpoint: <code>{{ url }}</code></p>
<pre id="s">loading...</pre>
<script>
async function refresh(){
 const r=await fetch('/status'); const s=await r.json();
 document.getElementById('s').textContent=JSON.stringify(s,null,2);
}
setInterval(refresh,1000); refresh();
</script></body></html>
"""


def md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def parse_challenge(text: str):
    import re
    m = re.search(r"realm=([^,\\s]+).*?nonce=([^,\\s]+).*?qop=([^,\\s]+)", text)
    if not m:
        raise RuntimeError(f"Cannot parse Digest challenge: {text}")
    return m.group(1), m.group(2), m.group(3)


def make_digest(realm, nonce, qop="auth"):
    # The browser request observed for this camera carries the Digest value
    # as a Cookie named Authorization. Keep the URI identical to Chrome's URI.
    uri = WS_URL
    nc = "00000001"
    cnonce = os.urandom(16).hex()
    ha1 = md5(f"{USERNAME}:{realm}:{PASSWORD}")
    ha2 = md5(f"GET:{uri}")
    response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    value = (
        'Digest '
        f'username="{USERNAME}", '
        f'realm="{realm}", '
        f'nonce="{nonce}", '
        'algorithm="MD5", '
        f'uri="{uri}", '
        f'response="{response}", '
        f'qop="{qop}", '
        f'nc="{nc}", '
        f'cnonce="{cnonce}"'
    )
    return value


def initial_challenge():
    ws = websocket.create_connection(WS_URL, origin=ORIGIN, timeout=10)
    state["connected"] = True
    msg = ws.recv()
    ws.close()
    if isinstance(msg, bytes):
        raise RuntimeError("Camera sent binary data before authentication")
    state["challenge"] = msg
    data = json.loads(msg)
    if data.get("errorCode") != 401:
        raise RuntimeError(f"Unexpected initial response: {msg}")
    return parse_challenge(data.get("detail", ""))


def authenticated_connect(auth_cookie):
    cookies = [f"Authorization={auth_cookie}"]
    if WEB_LOGIN_HANDLE:
        cookies.append(f"WebLoginHandle={WEB_LOGIN_HANDLE}")
    cookies.append("langInfo_=1")
    cookies.append("noShowTip=1")

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


def capture_loop():
    if not PASSWORD:
        state["last_error"] = "CAMERA_PASSWORD is not set"
        return

    try:
        realm, nonce, qop = initial_challenge()
        auth_cookie = make_digest(realm, nonce, qop)
        state["last_error"] = ""
        ws = authenticated_connect(auth_cookie)
        state["authenticated"] = True

        index = 0
        while True:
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
            path = CAPTURE_DIR / f"packet_{index:06d}.bin"
            path.write_bytes(packet)

            # Stop the local capture after 200 packets; this keeps the test bounded.
            if index >= 200:
                break
        ws.close()
    except Exception as exc:
        state["last_error"] = repr(exc)


@app.get("/")
def index():
    return render_template_string(PAGE, url=WS_URL)


@app.get("/status")
def status():
    return jsonify(state)


def main():
    print("=" * 70)
    print("Uniview WebSocket test viewer")
    print("=" * 70)
    print("Endpoint:", WS_URL)
    print("Capture directory:", CAPTURE_DIR.resolve())
    print()

    if not PASSWORD:
        print("Set CAMERA_PASSWORD before running.")
        print('PowerShell example: $env:CAMERA_PASSWORD="YOUR_PASSWORD"')
        return

    threading.Thread(target=capture_loop, daemon=True).start()

    url = f"http://127.0.0.1:{LOCAL_PORT}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=LOCAL_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
