import base64
import re
import socket
from urllib.parse import unquote


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


def probe(host, port, path, username="", password="", timeout=8):
    """Probe an RTSP endpoint from Android.

    Kotlin calls this function as:
        probe(host, port, path, username, password)
    """
    if not host:
        raise ValueError("RTSP host is missing")

    port = int(port)
    path = str(path or "/")
    if not path.startswith("/"):
        path = "/" + path

    uri = f"rtsp://{host}:{port}{path}"
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    cseq = 1

    def request(method, extra=""):
        nonlocal cseq
        auth = ""
        if username:
            token = base64.b64encode(
                f"{unquote(str(username))}:{unquote(str(password))}".encode()
            ).decode()
            auth = f"Authorization: Basic {token}\r\n"
        msg = (
            f"{method} {uri} RTSP/1.0\r\n"
            f"CSeq: {cseq}\r\n"
            f"User-Agent: MobileCameraAI-Python\r\n"
            f"{auth}{extra}\r\n"
        )
        cseq += 1
        sock.sendall(msg.encode())
        return _read_response(sock)

    try:
        options, _ = request("OPTIONS")
        describe, body = request("DESCRIBE", "Accept: application/sdp\r\n")
        sdp = body.decode("utf-8", "replace")
        first_options = options.splitlines()[0] if options else ""
        first_describe = describe.splitlines()[0] if describe else ""
        upper_sdp = sdp.upper()
        lower_sdp = sdp.lower()

        return {
            "ok": first_describe.startswith("RTSP/1.0 200"),
            "options": first_options,
            "describe": first_describe,
            "uri": uri,
            "sdp": sdp,
            "h265": "H265" in upper_sdp or "HEVC" in upper_sdp or "96 H265" in upper_sdp,
            "sprop_vps": "sprop-vps" in lower_sdp,
            "sprop_sps": "sprop-sps" in lower_sdp,
            "sprop_pps": "sprop-pps" in lower_sdp,
        }
    finally:
        sock.close()
