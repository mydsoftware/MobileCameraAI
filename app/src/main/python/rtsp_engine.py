import base64
import re
import socket
from urllib.parse import urlparse, unquote


def _read_response(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 1024 * 1024:
            raise RuntimeError("RTSP response too large")
    header, _, body = data.partition(b"\r\n\r\n")
    headers = header.decode("utf-8", "replace")
    m = re.search(r"Content-Length:\s*(\d+)", headers, re.I)
    if m:
        length = int(m.group(1))
        while len(body) < length:
            chunk = sock.recv(min(4096, length - len(body)))
            if not chunk:
                break
            body += chunk
    return headers, body


def probe(url, username, password, timeout=8):
    p = urlparse(url)
    if p.scheme.lower() != "rtsp":
        raise ValueError("URL must use rtsp://")
    host = p.hostname
    port = p.port or 554
    if not host:
        raise ValueError("RTSP host is missing")

    path = p.path or "/"
    if p.query:
        path += "?" + p.query
    uri = f"rtsp://{host}:{port}{path}"
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    cseq = 1

    def request(method, extra=""):
        nonlocal cseq
        auth = ""
        if username:
            token = base64.b64encode(f"{unquote(username)}:{unquote(password)}".encode()).decode()
            auth = f"Authorization: Basic {token}\r\n"
        msg = f"{method} {uri} RTSP/1.0\r\nCSeq: {cseq}\r\nUser-Agent: MobileCameraAI-Python\r\n{auth}{extra}\r\n"
        cseq += 1
        sock.sendall(msg.encode())
        return _read_response(sock)

    try:
        options, _ = request("OPTIONS")
        describe, body = request("DESCRIBE", "Accept: application/sdp\r\n")
        sdp = body.decode("utf-8", "replace")
        return {
            "ok": "200" in describe.splitlines()[0] if describe else False,
            "options": options.splitlines()[0] if options else "",
            "describe": describe.splitlines()[0] if describe else "",
            "sdp": sdp,
            "h265": "H265" in sdp.upper() or "HEVC" in sdp.upper() or "96 H265" in sdp.upper(),
            "sprop_vps": "sprop-vps" in sdp.lower(),
            "sprop_sps": "sprop-sps" in sdp.lower(),
            "sprop_pps": "sprop-pps" in sdp.lower(),
        }
    finally:
        sock.close()
